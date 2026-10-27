"""Combine the static schedule with realtime predictions into a departure board."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .gtfs_static import Route, StaticFeed, Stop
from .realtime import Alert, Prediction, Snapshot

# Keep showing a departure for this long after its time, so "Now" doesn't vanish early.
GRACE_SECONDS = 30
# Look this far back in the schedule so late-running buses are still found.
LOOKBACK_SECONDS = 45 * 60
# "Stops away" is noise beyond this; the bus is usually still on an earlier trip.
MAX_STOPS_AWAY = 25


def split_headsign(headsign: str, route: Route) -> tuple[str, str]:
    """Pull a leading route or branch number off a headsign.

    '5A - MEADOWLANDS TERMINAL' on route 5 -> ('5A', 'MEADOWLANDS TERMINAL').
    """
    match = re.match(rf"^\s*0*({re.escape(route.label)}[A-Z]?)\s*[-–:]\s*(.+)$", headsign)
    if match:
        return match[1], match[2]
    return route.label, headsign


@dataclass(frozen=True, slots=True)
class Departure:
    trip_id: str
    route: Route
    # Route number as shown on the bus, including any branch letter.
    badge: str
    headsign: str
    scheduled: int
    predicted: int | None
    # "live", "scheduled", "cancelled" or "skipped".
    status: str
    stops_away: int | None = None
    vehicle: str = ""
    occupancy: str | None = None

    @property
    def time(self) -> int:
        return self.predicted if self.predicted is not None else self.scheduled

    @property
    def delay_minutes(self) -> int | None:
        if self.predicted is None:
            return None
        return round((self.predicted - self.scheduled) / 60)

    @property
    def is_live(self) -> bool:
        return self.status == "live"

    @property
    def is_cancelled(self) -> bool:
        return self.status in ("cancelled", "skipped")


@dataclass(frozen=True, slots=True)
class Board:
    stop: Stop
    routes: list[Route]
    departures: list[Departure]
    alerts: list[tuple[Alert, str]]
    realtime: bool
    now: int
    # Set when the static schedule has no service today, e.g. the agency has only
    # published its next timetable.
    schedule_starts: date | None = None
    schedule_ended: date | None = None


def build_board(
    feed: StaticFeed,
    snapshot: Snapshot,
    stop: Stop,
    now: int,
    *,
    realtime: bool,
    limit: int = 10,
    horizon_seconds: int = 3 * 3600,
    only_routes: set[str] | None = None,
) -> Board:
    routes = feed.routes_for_stop(stop.stop_id)
    trips = snapshot.trips if realtime else {}
    vehicles = snapshot.vehicles if realtime else {}
    claimed: set[str] = set()
    departures: list[Departure] = []

    for s in feed.departures(stop.stop_id, now - LOOKBACK_SECONDS, now + horizon_seconds):
        if only_routes and s.route_id not in only_routes:
            continue
        route = feed.routes.get(s.route_id) or Route(s.route_id, s.route_id, "", "", "")
        prediction, predicted = None, None

        trip = trips.get(s.trip_id)
        if trip is not None:
            if trip.start_date:
                if trip.start_date != s.service_date.strftime("%Y%m%d"):
                    trip = None
            elif s.trip_id in claimed:
                # Without a start date, the prediction belongs to the earliest run.
                trip = None
        if trip is not None:
            claimed.add(s.trip_id)
            prediction, predicted = trip.at(s.stop_sequence, stop.stop_id, s.scheduled)

        vehicle = vehicles.get(s.trip_id) if trip is not None else None
        stops_away = None
        if vehicle is not None and vehicle.current_stop_sequence is not None:
            stops_away = s.stop_sequence - vehicle.current_stop_sequence
            if stops_away < 0 and prediction is None:
                prediction = Prediction.PASSED
            if not 0 <= stops_away <= MAX_STOPS_AWAY:
                stops_away = None

        if prediction is Prediction.PASSED:
            continue
        status = prediction.value if prediction is not None else "scheduled"
        badge, headsign = split_headsign(s.headsign, route)
        departure = Departure(
            trip_id=s.trip_id,
            route=route,
            badge=badge,
            headsign=headsign,
            scheduled=s.scheduled,
            predicted=predicted,
            status=status,
            stops_away=stops_away,
            vehicle=vehicle.label if vehicle else "",
            occupancy=vehicle.occupancy if vehicle else None,
        )
        if now - GRACE_SECONDS <= departure.time <= now + horizon_seconds:
            departures.append(departure)

    departures.sort(key=lambda d: (d.time, d.route.label))

    route_ids = {r.route_id for r in routes}
    scopes = {"stop": 0, "route": 1, "agency": 2}
    alerts = [
        (alert, scope)
        for alert in snapshot.alerts
        if alert.active(now) and (scope := alert.scope(stop.stop_id, route_ids))
    ]
    alerts.sort(key=lambda pair: scopes[pair[1]])

    today = datetime.fromtimestamp(now, feed.tz).date()
    starts = feed.first_day if feed.first_day and today < feed.first_day else None
    ended = feed.last_day if feed.last_day and today > feed.last_day else None

    return Board(
        stop=stop,
        routes=routes,
        departures=departures[:limit],
        alerts=alerts,
        realtime=realtime,
        now=now,
        schedule_starts=starts,
        schedule_ended=ended,
    )
