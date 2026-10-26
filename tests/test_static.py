from datetime import date, datetime

from conftest import TZ, local_ts

from nextbus.gtfs_static import parse_gtfs_time, service_day_start

MONDAY = (2026, 10, 26)


def test_parse_gtfs_time_handles_padding_and_overnight():
    assert parse_gtfs_time(" 4:30:00") == 4 * 3600 + 30 * 60
    assert parse_gtfs_time("25:10:05") == 25 * 3600 + 10 * 60 + 5
    assert parse_gtfs_time("") is None


def test_service_day_starts_at_midnight_on_normal_days():
    assert service_day_start(date(*MONDAY), TZ) == local_ts(*MONDAY)


def test_service_day_start_is_noon_minus_12h_when_clocks_change():
    # Clocks fall back at 02:00 on 2026-11-01, so noon minus 12h is 01:00 EDT, not midnight.
    expected = datetime(2026, 11, 1, 1, 0, tzinfo=TZ).timestamp()
    assert service_day_start(date(2026, 11, 1), TZ) == expected


def test_feed_metadata(feed):
    assert feed.meta["feed_version"] == "test-1"
    assert feed.tz.key == "America/Toronto"


def test_services_follow_calendar_and_exceptions(feed):
    assert feed.services_on(date(*MONDAY)) == {"WK"}
    assert feed.services_on(date(2026, 10, 31)) == {"SAT"}
    assert feed.services_on(date(2026, 11, 1)) == set()
    # Holiday Monday runs Saturday service instead.
    assert feed.services_on(date(2026, 10, 12)) == {"SAT"}


def test_departures_only_include_running_services(feed):
    deps = feed.departures("S1", local_ts(*MONDAY, 7, 50), local_ts(*MONDAY, 9, 30))
    assert [d.trip_id for d in deps] == ["T1", "T5", "T2"]
    assert deps[0].scheduled == local_ts(*MONDAY, 8, 0)
    assert deps[0].service_date == date(*MONDAY)


def test_departures_on_holiday_use_replacement_service(feed):
    deps = feed.departures("S1", local_ts(2026, 10, 12, 7), local_ts(2026, 10, 12, 10))
    assert [d.trip_id for d in deps] == ["T3"]


def test_departures_after_midnight_belong_to_previous_service_day(feed):
    deps = feed.departures("S1", local_ts(2026, 10, 27, 0, 0), local_ts(2026, 10, 27, 1, 0))
    assert [d.trip_id for d in deps] == ["T4"]
    assert deps[0].service_date == date(*MONDAY)
    assert deps[0].scheduled == local_ts(2026, 10, 27, 0, 30)


def test_departures_skip_drop_off_only_stops(feed):
    # S3 is the last stop of every trip; T5 has pickup_type=1 at S2.
    assert feed.departures("S3", local_ts(*MONDAY, 7), local_ts(*MONDAY, 10)) == []
    deps = feed.departures("S2", local_ts(*MONDAY, 7), local_ts(*MONDAY, 10))
    assert "T5" not in {d.trip_id for d in deps}


def test_search_by_stop_code(feed):
    results = feed.search("1002")
    assert results[0].stop_id == "S2"


def test_search_matches_all_words_and_prefers_prefix(feed):
    names = [s.name for s in feed.search("king james")]
    assert names == ["KING at JAMES", "JAMES at KING"]


def test_search_excludes_stations(feed):
    assert feed.search("central") == []


def test_nearby_sorted_by_distance(feed):
    results = feed.nearby(43.2571, -79.8689, radius_m=500)
    assert [s.stop_id for s, _ in results][:2] in (["S1", "S2"], ["S2", "S1"])
    assert all(d <= 500 for _, d in results)
    assert "ST1" not in {s.stop_id for s, _ in results}


def test_routes_for_stop_sorted_naturally(feed):
    assert [r.label for r in feed.routes_for_stop("S1")] == ["1", "5"]
    assert feed.routes["R1"].css_color == "#0093DD"
    assert feed.routes["R5"].css_color == ""
