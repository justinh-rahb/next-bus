"""GTFS-Realtime: poll trip updates, vehicle positions and alerts into an immutable snapshot.

Feeds are fetched once per poll interval and shared by every request. Polling only runs while
people are looking at stops; the first request after an idle period fetches on demand.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

import httpx
from google.transit import gtfs_realtime_pb2 as pb

log = logging.getLogger(__name__)

TripDescriptor = pb.TripDescriptor
StopTimeUpdate = pb.TripUpdate.StopTimeUpdate


class Prediction(Enum):
    LIVE = "live"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    # The vehicle is already past this stop.
    PASSED = "passed"


@dataclass(frozen=True, slots=True)
class StopTimePrediction:
    stop_sequence: int | None
    stop_id: str
    time: int | None
    delay: int | None
    skipped: bool


@dataclass(frozen=True, slots=True)
class TripPrediction:
    trip_id: str
    start_date: str
    cancelled: bool
    updates: tuple[StopTimePrediction, ...]

    def at(
        self, stop_sequence: int, stop_id: str, scheduled: int
    ) -> tuple[Prediction | None, int | None]:
        """Predicted departure for one stop of this trip, or (None, None) if unknown."""
        if self.cancelled:
            return Prediction.CANCELLED, None
        prior: StopTimePrediction | None = None
        for u in self.updates:
            matches = (
                u.stop_sequence == stop_sequence
                if u.stop_sequence is not None
                else (u.stop_id == stop_id)
            )
            if matches:
                if u.skipped:
                    return Prediction.SKIPPED, None
                if u.time:
                    return Prediction.LIVE, u.time
                if u.delay is not None:
                    return Prediction.LIVE, scheduled + u.delay
                break
            if u.stop_sequence is not None and u.stop_sequence < stop_sequence:
                prior = u
        else:
            # Agencies drop stops the vehicle has left, so updates that all come after this
            # stop mean the bus has gone.
            seqs = [u.stop_sequence for u in self.updates if u.stop_sequence is not None]
            if seqs and min(seqs) > stop_sequence:
                return Prediction.PASSED, None
        # Delays carry forward to later stops until the next update (GTFS-RT spec).
        if prior is not None and prior.delay is not None:
            return Prediction.LIVE, scheduled + prior.delay
        return None, None


@dataclass(frozen=True, slots=True)
class Vehicle:
    trip_id: str
    label: str
    current_stop_sequence: int | None
    at_stop: bool
    occupancy: str | None
    timestamp: int


@dataclass(frozen=True, slots=True)
class InformedEntity:
    route_id: str
    stop_id: str
    trip_id: str


@dataclass(frozen=True, slots=True)
class Alert:
    alert_id: str
    header: str
    description: str
    effect: str
    severity: str
    url: str
    periods: tuple[tuple[int, int], ...]
    entities: tuple[InformedEntity, ...]

    def active(self, now: float) -> bool:
        return not self.periods or any(
            (start or 0) <= now <= (end or float("inf")) for start, end in self.periods
        )

    def scope(self, stop_id: str, route_ids: set[str]) -> str | None:
        """'stop', 'route' or 'agency' if the alert is relevant to this stop, else None."""
        best = None
        for e in self.entities:
            if e.stop_id == stop_id and (not e.route_id or e.route_id in route_ids):
                return "stop"
            if not e.stop_id and e.route_id in route_ids and not e.trip_id:
                best = "route"
            elif not (e.stop_id or e.route_id or e.trip_id) and best is None:
                best = "agency"
        return best


@dataclass(frozen=True)
class Snapshot:
    trips: dict[str, TripPrediction] = field(default_factory=dict)
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    alerts: tuple[Alert, ...] = ()
    # Feed header time of the trip updates feed, epoch seconds.
    feed_time: int | None = None
    fetched_at: float | None = None


def _text(translated: pb.TranslatedString) -> str:
    texts = list(translated.translation)
    if not texts:
        return ""
    for t in texts:
        if t.language in ("", "en") or t.language.startswith("en-"):
            return t.text.strip()
    return texts[0].text.strip()


def _enum_name(enum, value: int) -> str:
    return enum.Name(value).lower().replace("_", " ")


def parse_trip_updates(data: bytes) -> tuple[dict[str, TripPrediction], int]:
    feed = pb.FeedMessage()
    feed.ParseFromString(data)
    trips = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        updates = []
        for stu in tu.stop_time_update:
            event = stu.departure if stu.HasField("departure") else stu.arrival
            delay = None
            if event.HasField("delay"):
                delay = event.delay
            elif event.time and event.HasField("scheduled_time"):
                delay = event.time - event.scheduled_time
            updates.append(
                StopTimePrediction(
                    stop_sequence=stu.stop_sequence if stu.HasField("stop_sequence") else None,
                    stop_id=stu.stop_id,
                    time=event.time or None,
                    delay=delay,
                    skipped=stu.schedule_relationship == StopTimeUpdate.SKIPPED,
                )
            )
        rel = tu.trip.schedule_relationship
        trips[tu.trip.trip_id] = TripPrediction(
            trip_id=tu.trip.trip_id,
            start_date=tu.trip.start_date,
            cancelled=rel in (TripDescriptor.CANCELED, getattr(TripDescriptor, "DELETED", -1)),
            updates=tuple(updates),
        )
    return trips, feed.header.timestamp


def parse_vehicle_positions(data: bytes) -> dict[str, Vehicle]:
    feed = pb.FeedMessage()
    feed.ParseFromString(data)
    vehicles = {}
    VP = pb.VehiclePosition
    for entity in feed.entity:
        if not entity.HasField("vehicle") or not entity.vehicle.trip.trip_id:
            continue
        v = entity.vehicle
        occupancy = None
        if v.HasField("occupancy_status") and v.occupancy_status != VP.NO_DATA_AVAILABLE:
            occupancy = _enum_name(VP.OccupancyStatus, v.occupancy_status)
        vehicles[v.trip.trip_id] = Vehicle(
            trip_id=v.trip.trip_id,
            label=v.vehicle.label or v.vehicle.id,
            current_stop_sequence=(
                v.current_stop_sequence if v.HasField("current_stop_sequence") else None
            ),
            at_stop=v.current_status == VP.STOPPED_AT,
            occupancy=occupancy,
            timestamp=v.timestamp,
        )
    return vehicles


def parse_alerts(data: bytes) -> tuple[Alert, ...]:
    feed = pb.FeedMessage()
    feed.ParseFromString(data)
    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert") or entity.is_deleted:
            continue
        a = entity.alert
        alerts.append(
            Alert(
                alert_id=entity.id,
                header=_text(a.header_text),
                description=_text(a.description_text).replace("\r\n", "\n"),
                effect=_enum_name(pb.Alert.Effect, a.effect) if a.HasField("effect") else "",
                severity=(
                    _enum_name(pb.Alert.SeverityLevel, a.severity_level)
                    if a.HasField("severity_level")
                    else ""
                ),
                url=_text(a.url),
                periods=tuple((p.start, p.end) for p in a.active_period),
                entities=tuple(
                    InformedEntity(e.route_id, e.stop_id, e.trip.trip_id) for e in a.informed_entity
                ),
            )
        )
    return tuple(alerts)


class RealtimePoller:
    def __init__(
        self,
        client: httpx.AsyncClient,
        trip_updates_url: str,
        vehicle_positions_url: str = "",
        alerts_url: str = "",
        poll_seconds: int = 20,
        idle_seconds: int = 300,
        stale_seconds: int = 180,
    ):
        self.client = client
        self.urls = (trip_updates_url, vehicle_positions_url, alerts_url)
        self.poll_seconds = poll_seconds
        self.idle_seconds = idle_seconds
        self.stale_seconds = stale_seconds
        self.snapshot = Snapshot()
        self._last_demand = 0.0
        self._last_attempt = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.urls[0])

    def is_fresh(self, snapshot: Snapshot, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        stamp = snapshot.feed_time or snapshot.fetched_at
        return stamp is not None and now - stamp <= self.stale_seconds

    async def get(self) -> Snapshot:
        """Current snapshot, refreshing first if it's older than one poll interval."""
        self._last_demand = time.monotonic()
        if self.enabled and time.monotonic() - self._last_attempt >= self.poll_seconds:
            try:
                await asyncio.wait_for(self._refresh_if_due(), timeout=8)
            except TimeoutError:
                log.warning("Realtime fetch is slow; serving previous data")
        return self.snapshot

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_seconds)
            if time.monotonic() - self._last_demand < self.idle_seconds:
                await self._refresh_if_due()

    async def _refresh_if_due(self) -> None:
        async with self._lock:
            if time.monotonic() - self._last_attempt < self.poll_seconds:
                return
            self._last_attempt = time.monotonic()
            await self.refresh()

    async def _fetch(self, url: str) -> bytes | None:
        if not url:
            return None
        try:
            response = await self.client.get(url, timeout=10)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            log.warning("Realtime fetch failed for %s: %s", url, exc)
            return None

    async def refresh(self) -> None:
        trips_raw, vehicles_raw, alerts_raw = await asyncio.gather(
            *(self._fetch(url) for url in self.urls)
        )
        old = self.snapshot

        def parse() -> Snapshot:
            trips, feed_time = old.trips, old.feed_time
            vehicles, alerts = old.vehicles, old.alerts
            fetched_at = old.fetched_at
            try:
                if trips_raw is not None:
                    trips, feed_time = parse_trip_updates(trips_raw)
                    fetched_at = time.time()
                if vehicles_raw is not None:
                    vehicles = parse_vehicle_positions(vehicles_raw)
                if alerts_raw is not None:
                    alerts = parse_alerts(alerts_raw)
            except Exception:
                log.exception("Could not parse realtime feed")
            return Snapshot(trips, vehicles, alerts, feed_time or None, fetched_at)

        self.snapshot = await asyncio.to_thread(parse)
