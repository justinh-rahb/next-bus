from conftest import local_ts
from google.transit import gtfs_realtime_pb2 as pb

from nextbus.onboard import build_trip_view, nearby_buses
from nextbus.realtime import Snapshot, Vehicle, parse_trip_updates

MONDAY = (2026, 10, 26)


def at(hh, mm):
    return local_ts(*MONDAY, hh, mm)


def bus(trip_id="T1", seq=None, at_stop=False, timestamp=None, lat=None, lon=None, label="2313"):
    return Vehicle(
        trip_id=trip_id,
        label=label,
        current_stop_sequence=seq,
        at_stop=at_stop,
        occupancy=None,
        timestamp=timestamp or at(8, 0),
        vehicle_id="3713",
        lat=lat,
        lon=lon,
    )


def trip_updates(trip_id, *updates, start_date="20261026"):
    """updates: (seq, stop_id, time, skipped)."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = at(8, 0)
    tu = feed.entity.add(id=trip_id).trip_update
    tu.trip.trip_id = trip_id
    tu.trip.start_date = start_date
    for seq, stop_id, time, skipped in updates:
        stu = tu.stop_time_update.add(stop_sequence=seq, stop_id=stop_id)
        if skipped:
            stu.schedule_relationship = pb.TripUpdate.StopTimeUpdate.SKIPPED
        else:
            stu.departure.time = time
    trips, feed_time = parse_trip_updates(feed.SerializeToString())
    return trips


def states(view):
    return [(s.stop.stop_id, s.state) for s in view.stops]


def test_timetable_alone_picks_the_next_stop(feed):
    view = build_trip_view(feed, Snapshot(), "T1", at(8, 3), realtime=False)
    assert (view.badge, view.headsign) == ("1A", "WEST LOOP")
    assert states(view) == [("S1", "passed"), ("S2", "next"), ("S3", "upcoming")]
    assert view.next_stop.scheduled == at(8, 5)
    assert view.next_stop.predicted is None


def test_trip_is_finished_after_its_last_stop(feed):
    view = build_trip_view(feed, Snapshot(), "T1", at(8, 30), realtime=False)
    assert view.finished
    assert view.next_stop is None


def test_vehicle_position_beats_the_timetable(feed):
    # The timetable says S2 is next, but the bus reports it's already heading to S3.
    snapshot = Snapshot(vehicles={"T1": bus(seq=3)})
    view = build_trip_view(feed, snapshot, "T1", at(8, 3), realtime=True)
    assert states(view) == [("S1", "passed"), ("S2", "passed"), ("S3", "next")]
    assert view.vehicle.label == "2313"

    stopped = Snapshot(vehicles={"T1": bus(seq=2, at_stop=True)})
    view = build_trip_view(feed, stopped, "T1", at(8, 3), realtime=True)
    assert view.next_stop.state == "at"
    assert view.next_stop.stop.stop_id == "S2"


def test_live_predictions_find_the_next_stop_without_a_vehicle_feed(feed):
    # Like MiWay: no current stop on the vehicle, but passed stops drop out of the update.
    trips = trip_updates("T1", (2, "S2", at(8, 7), False), (3, "S3", at(8, 12), False))
    view = build_trip_view(feed, Snapshot(trips=trips), "T1", at(8, 1), realtime=True)
    assert states(view)[:2] == [("S1", "passed"), ("S2", "next")]
    assert view.next_stop.predicted == at(8, 7)
    assert view.delay_minutes == 2


def test_skipped_stop_is_marked(feed):
    trips = trip_updates("T1", (2, "S2", None, True), (3, "S3", at(8, 10), False))
    view = build_trip_view(feed, Snapshot(trips=trips), "T1", at(8, 1), realtime=True)
    s2 = next(s for s in view.stops if s.stop.stop_id == "S2")
    assert s2.skipped
    # A skipped stop isn't where the bus is heading next.
    assert view.next_stop.stop.stop_id == "S3"


def test_overnight_trip_uses_the_previous_service_day(feed):
    view = build_trip_view(feed, Snapshot(), "T4", local_ts(2026, 10, 27, 0, 20), realtime=False)
    assert view.next_stop.stop.stop_id == "S1"
    assert view.next_stop.scheduled == local_ts(2026, 10, 27, 0, 30)


def test_unknown_trip(feed):
    assert build_trip_view(feed, Snapshot(), "nope", at(8, 0), realtime=False) is None


def test_snapshot_finds_a_bus_by_fleet_number_or_feed_id():
    snapshot = Snapshot(vehicles={"T1": bus()})
    assert snapshot.vehicle("2313").trip_id == "T1"
    assert snapshot.vehicle("#2313").trip_id == "T1"
    assert snapshot.vehicle("3713").trip_id == "T1"
    assert snapshot.vehicle("9999") is None
    assert snapshot.vehicle("") is None


def test_nearby_buses_skips_stale_and_far_away_vehicles(feed):
    near = bus("T1", seq=2, timestamp=at(8, 2), lat=43.2571, lon=-79.8689)
    stale = bus("T2", seq=1, timestamp=at(7, 0), lat=43.2571, lon=-79.8689, label="2400")
    far = bus("T5", seq=1, timestamp=at(8, 2), lat=43.30, lon=-79.80, label="2500")
    snapshot = Snapshot(vehicles={"T1": near, "T2": stale, "T5": far})
    found = nearby_buses(feed, snapshot, 43.2570, -79.8690, at(8, 3))
    assert [b.vehicle.label for b in found] == ["2313"]
    assert found[0].view.next_stop.stop.stop_id == "S2"
    assert found[0].distance_m < 50


def test_service_date_from_live_start_date(feed):
    trips = trip_updates("T1", (1, "S1", at(8, 1), False), start_date="20261026")
    view = build_trip_view(feed, Snapshot(trips=trips), "T1", at(7, 50), realtime=True)
    assert view.stops[0].scheduled == at(8, 0)
    # The day before has the same trip; the start date picks this run.
    assert view.next_stop.predicted == at(8, 1)
