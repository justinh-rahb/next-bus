from conftest import local_ts
from google.transit import gtfs_realtime_pb2 as pb

from nextbus.arrivals import build_board, split_headsign
from nextbus.gtfs_static import Route
from nextbus.realtime import (
    Alert,
    InformedEntity,
    Snapshot,
    parse_alerts,
    parse_trip_updates,
    parse_vehicle_positions,
)

MONDAY = (2026, 10, 26)
NOW = local_ts(*MONDAY, 7, 55)


def trip_updates(*trips) -> bytes:
    """trips: (trip_id, start_date, relationship, [(seq, stop_id, time, delay, skipped)])."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = NOW
    for trip_id, start_date, rel, updates in trips:
        tu = feed.entity.add(id=trip_id).trip_update
        tu.trip.trip_id = trip_id
        if start_date:
            tu.trip.start_date = start_date
        tu.trip.schedule_relationship = rel
        for seq, stop_id, time, delay, skipped in updates:
            stu = tu.stop_time_update.add(stop_sequence=seq, stop_id=stop_id)
            if skipped:
                stu.schedule_relationship = pb.TripUpdate.StopTimeUpdate.SKIPPED
                continue
            if time is not None:
                stu.departure.time = time
            if delay is not None:
                stu.departure.delay = delay
    return feed.SerializeToString()


SCHEDULED = pb.TripDescriptor.SCHEDULED


EMPTY = Snapshot()


def board(feed, snapshot=EMPTY, stop_id="S1", now=NOW, **kwargs):
    stop = feed.stop(stop_id)
    return build_board(feed, snapshot, stop, now, realtime=True, **kwargs)


def snapshot(raw: bytes, vehicles=None, alerts=()) -> Snapshot:
    trips, feed_time = parse_trip_updates(raw)
    return Snapshot(trips, vehicles or {}, alerts, feed_time, NOW)


def test_schedule_only(feed):
    b = board(feed)
    assert [(d.trip_id, d.status) for d in b.departures] == [
        ("T1", "scheduled"),
        ("T5", "scheduled"),
        ("T2", "scheduled"),
    ]


def test_live_time_replaces_schedule_and_reorders(feed):
    raw = trip_updates(
        ("T1", "20261026", SCHEDULED, [(1, "S1", local_ts(*MONDAY, 8, 25), None, False)])
    )
    deps = board(feed, snapshot(raw)).departures
    assert [d.trip_id for d in deps] == ["T5", "T1", "T2"]
    t1 = deps[1]
    assert t1.status == "live"
    assert t1.delay_minutes == 25


def test_delay_propagates_from_earlier_stop(feed):
    raw = trip_updates(("T2", "20261026", SCHEDULED, [(1, "S1", None, 120, False)]))
    t2 = next(d for d in board(feed, snapshot(raw), stop_id="S2").departures if d.trip_id == "T2")
    assert t2.predicted == local_ts(*MONDAY, 8, 37)


def test_trip_already_past_stop_is_dropped(feed):
    # Only later stops remain in the update, so the bus has left S1.
    raw = trip_updates(
        ("T1", "20261026", SCHEDULED, [(2, "S2", local_ts(*MONDAY, 8, 6), None, False)])
    )
    ids = [d.trip_id for d in board(feed, snapshot(raw), now=local_ts(*MONDAY, 7, 59)).departures]
    assert "T1" not in ids


def test_skipped_and_cancelled(feed):
    raw = trip_updates(
        ("T1", "20261026", SCHEDULED, [(1, "S1", None, None, True)]),
        ("T5", "20261026", pb.TripDescriptor.CANCELED, []),
    )
    status = {d.trip_id: d.status for d in board(feed, snapshot(raw)).departures}
    assert status["T1"] == "skipped"
    assert status["T5"] == "cancelled"


def test_prediction_for_another_service_day_is_ignored(feed):
    raw = trip_updates(
        ("T1", "20261027", SCHEDULED, [(1, "S1", local_ts(*MONDAY, 8, 25), None, False)])
    )
    t1 = board(feed, snapshot(raw)).departures[0]
    assert (t1.trip_id, t1.status) == ("T1", "scheduled")


def test_stale_realtime_is_not_used(feed):
    raw = trip_updates(("T1", "20261026", SCHEDULED, [(1, "S1", None, None, True)]))
    stop = feed.stop("S1")
    b = build_board(feed, snapshot(raw), stop, NOW, realtime=False)
    assert b.departures[0].status == "scheduled"
    assert b.realtime is False


def test_vehicle_position_gives_stops_away(feed):
    raw = trip_updates(("T2", "20261026", SCHEDULED, [(1, "S1", None, 0, False)]))
    vp = pb.FeedMessage()
    vp.header.gtfs_realtime_version = "2.0"
    v = vp.entity.add(id="bus").vehicle
    v.trip.trip_id = "T2"
    v.vehicle.label = "2313"
    v.current_stop_sequence = 1
    vehicles = parse_vehicle_positions(vp.SerializeToString())
    t2 = next(
        d
        for d in board(feed, snapshot(raw, vehicles), stop_id="S2").departures
        if d.trip_id == "T2"
    )
    assert t2.stops_away == 1
    assert t2.vehicle == "2313"


def test_limit_and_route_filter(feed):
    assert len(board(feed, limit=2).departures) == 2
    assert {d.route.route_id for d in board(feed, only_routes={"R5"}).departures} == {"R5"}


def test_split_headsign():
    route = Route("R1", "01", "KING", "", "")
    assert split_headsign("1A - WEST LOOP", route) == ("1A", "WEST LOOP")
    assert split_headsign("01 - NIGHT OWL", route) == ("1", "NIGHT OWL")
    assert split_headsign("WEST LOOP", route) == ("1", "WEST LOOP")
    assert split_headsign("10 - ELSEWHERE", route) == ("1", "10 - ELSEWHERE")


def make_alert(*entities, periods=()):
    return Alert("a", "Detour", "", "detour", "", "", periods, tuple(entities))


def test_alert_scope():
    routes = {"R1"}
    assert make_alert(InformedEntity("", "S1", "")).scope("S1", routes) == "stop"
    assert make_alert(InformedEntity("R1", "", "")).scope("S1", routes) == "route"
    assert make_alert(InformedEntity("", "", "")).scope("S1", routes) == "agency"
    assert make_alert(InformedEntity("R9", "", "")).scope("S1", routes) is None
    assert make_alert(InformedEntity("R9", "S1", "")).scope("S1", routes) is None


def test_alert_active_periods():
    alert = make_alert(InformedEntity("", "", ""), periods=((100, 200),))
    assert alert.active(150)
    assert not alert.active(250)
    assert make_alert(InformedEntity("", "", "")).active(10**12)


def test_board_includes_relevant_alerts(feed):
    raw = pb.FeedMessage()
    raw.header.gtfs_realtime_version = "2.0"
    a = raw.entity.add(id="9").alert
    a.header_text.translation.add(text="Stop closed", language="en")
    a.informed_entity.add(stop_id="S1")
    b = raw.entity.add(id="10").alert
    b.header_text.translation.add(text="Other stop", language="en")
    b.informed_entity.add(stop_id="S3", route_id="R9")
    alerts = parse_alerts(raw.SerializeToString())
    result = board(feed, Snapshot(alerts=alerts)).alerts
    assert [(alert.header, scope) for alert, scope in result] == [("Stop closed", "stop")]


def test_board_explains_when_schedule_does_not_cover_today(feed):
    assert (feed.first_day.isoformat(), feed.last_day.isoformat()) == ("2026-01-01", "2026-12-31")
    assert board(feed).schedule_starts is None
    later = board(feed, now=local_ts(2027, 1, 4, 8, 0))
    assert later.departures == []
    assert later.schedule_ended.isoformat() == "2026-12-31"
    earlier = board(feed, now=local_ts(2025, 12, 1, 8, 0))
    assert earlier.schedule_starts.isoformat() == "2026-01-01"
