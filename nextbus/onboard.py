"""The rider-on-the-bus view: which trip this is, the next stop, and when we'll get there.

Where the bus is in its trip comes from the best source available:
1. the vehicle feed's current stop (HSR reports this),
2. otherwise the trip's live predictions, since agencies drop stops a bus has passed
   (MiWay works this way),
3. otherwise the timetable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .arrivals import GRACE_SECONDS, split_headsign
from .gtfs_static import Route, StaticFeed, Stop, haversine_m, service_day_start
from .realtime import Prediction, Snapshot, TripPrediction, Vehicle

# Treat a vehicle position older than this as stale when finding buses near a rider.
VEHICLE_MAX_AGE = 300


@dataclass(frozen=True, slots=True)
class TripStop:
    stop: Stop
    sequence: int
    scheduled: int
    predicted: int | None
    skipped: bool
    # "passed", "at" (the bus is stopped here), "next" or "upcoming".
    state: str

    @property
    def time(self) -> int:
        return self.predicted if self.predicted is not None else self.scheduled


@dataclass(frozen=True, slots=True)
class TripView:
    trip_id: str
    route: Route
    badge: str
    headsign: str
    stops: list[TripStop]
    vehicle: Vehicle | None
    realtime: bool
    cancelled: bool
    now: int

    @property
    def next_stop(self) -> TripStop | None:
        return next((s for s in self.stops if s.state in ("at", "next")), None)

    @property
    def upcoming(self) -> list[TripStop]:
        """The next stop and everything after it."""
        return [s for s in self.stops if s.state != "passed"]

    @property
    def passed(self) -> list[TripStop]:
        return [s for s in self.stops if s.state == "passed"]

    @property
    def finished(self) -> bool:
        return self.next_stop is None

    @property
    def delay_minutes(self) -> int | None:
        nxt = self.next_stop
        if nxt is None or nxt.predicted is None:
            return None
        return round((nxt.predicted - nxt.scheduled) / 60)


def _service_date(
    feed: StaticFeed, service_id: str, first: int, last: int, now: int, hint: str
) -> date:
    """The service day this run of the trip belongs to."""
    if hint:
        try:
            return datetime.strptime(hint, "%Y%m%d").date()
        except ValueError:
            pass
    today = datetime.fromtimestamp(now, feed.tz).date()
    candidates = [today, today - timedelta(days=1), today + timedelta(days=1)]
    running = [d for d in candidates if service_id in feed.services_on(d)]
    # Prefer the run that's under way (or about to be) right now.
    for day in running:
        base = service_day_start(day, feed.tz)
        if base + first - 3600 <= now <= base + last + 3600:
            return day
    return running[0] if running else today


def _next_sequence(
    stops: list[tuple[int, int, Stop]],
    times: dict[int, int],
    prediction: TripPrediction | None,
    vehicle: Vehicle | None,
    now: int,
) -> tuple[int | None, bool]:
    """(stop_sequence of the next stop, whether the bus is stopped at it)."""
    if vehicle is not None and vehicle.current_stop_sequence is not None:
        return vehicle.current_stop_sequence, vehicle.at_stop
    if prediction is not None and prediction.updates and not prediction.cancelled:
        by_stop = {stop.stop_id: seq for seq, _, stop in stops}
        live = [
            u.stop_sequence if u.stop_sequence is not None else by_stop.get(u.stop_id)
            for u in prediction.updates
            if not u.skipped and (u.time is None or u.time >= now - GRACE_SECONDS)
        ]
        live = [seq for seq in live if seq is not None]
        if live:
            return min(live), False
    for seq, _, _ in stops:
        if times[seq] >= now - GRACE_SECONDS:
            return seq, False
    return None, False


def build_trip_view(
    feed: StaticFeed,
    snapshot: Snapshot,
    trip_id: str,
    now: int,
    *,
    realtime: bool,
    vehicle: Vehicle | None = None,
) -> TripView | None:
    info = feed.trip(trip_id)
    stops = feed.trip_stops(trip_id)
    if info is None or not stops:
        return None
    route_id, service_id, raw_headsign = info
    route = feed.routes.get(route_id) or Route(route_id, route_id, "", "", "")
    badge, headsign = split_headsign(raw_headsign, route)

    prediction = snapshot.trips.get(trip_id) if realtime else None
    if vehicle is None and realtime:
        vehicle = snapshot.vehicles.get(trip_id)
    hint = (prediction.start_date if prediction else "") or (vehicle.start_date if vehicle else "")
    day = _service_date(feed, service_id, stops[0][1], stops[-1][1], now, hint)
    base = service_day_start(day, feed.tz)

    predicted: dict[int, int | None] = {}
    skipped: set[int] = set()
    for seq, dep, stop in stops:
        state, when = (None, None)
        if prediction is not None:
            state, when = prediction.at(seq, stop.stop_id, base + dep)
        predicted[seq] = when if state is Prediction.LIVE else None
        if state is Prediction.SKIPPED:
            skipped.add(seq)
    times = {seq: predicted[seq] or base + dep for seq, dep, _ in stops}

    next_seq, at_stop = _next_sequence(stops, times, prediction, vehicle, now)
    trip_stops = []
    for seq, dep, stop in stops:
        if next_seq is None or seq < next_seq:
            state = "passed"
        elif seq == next_seq:
            state = "at" if at_stop else "next"
        else:
            state = "upcoming"
        trip_stops.append(TripStop(stop, seq, base + dep, predicted[seq], seq in skipped, state))

    return TripView(
        trip_id=trip_id,
        route=route,
        badge=badge,
        headsign=headsign,
        stops=trip_stops,
        vehicle=vehicle,
        realtime=realtime,
        cancelled=bool(prediction and prediction.cancelled),
        now=now,
    )


@dataclass(frozen=True, slots=True)
class NearbyBus:
    vehicle: Vehicle
    distance_m: float
    view: TripView


def nearby_buses(
    feed: StaticFeed,
    snapshot: Snapshot,
    lat: float,
    lon: float,
    now: int,
    *,
    radius_m: float = 500,
    limit: int = 6,
) -> list[NearbyBus]:
    """In-service buses close to a rider, nearest first.

    The radius is generous because vehicle positions are often 30 seconds or more old,
    and a bus covers a few hundred metres in that time.
    """
    found = []
    for vehicle in snapshot.vehicles.values():
        if vehicle.lat is None or vehicle.lon is None or now - vehicle.timestamp > VEHICLE_MAX_AGE:
            continue
        distance = haversine_m(lat, lon, vehicle.lat, vehicle.lon)
        if distance <= radius_m:
            found.append((distance, vehicle))
    found.sort(key=lambda pair: pair[0])
    out = []
    for distance, vehicle in found:
        view = build_trip_view(feed, snapshot, vehicle.trip_id, now, realtime=True, vehicle=vehicle)
        if view is not None and not view.finished:
            out.append(NearbyBus(vehicle, distance, view))
        if len(out) == limit:
            break
    return out
