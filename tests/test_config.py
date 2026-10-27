from nextbus.config import Settings


def clear(monkeypatch):
    for name in (
        "GTFS_TRIP_UPDATES_URL",
        "GTFS_REALTIME_URL",
        "GTFS_VEHICLE_POSITIONS_URL",
        "GTFS_ALERTS_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_hsr(monkeypatch):
    clear(monkeypatch)
    s = Settings.from_env()
    assert s.vehicle_positions_url == Settings.vehicle_positions_url
    assert s.alerts_url == Settings.alerts_url
    assert s.guessed_feeds == frozenset()


def test_other_agency_gets_sibling_feeds_not_hsr(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("GTFS_REALTIME_URL", "https://example.ca/gtfs-rt/GTFS_TripUpdates.pb")
    s = Settings.from_env()
    assert s.trip_updates_url == "https://example.ca/gtfs-rt/GTFS_TripUpdates.pb"
    assert s.vehicle_positions_url == "https://example.ca/gtfs-rt/GTFS_VehiclePositions.pb"
    assert s.alerts_url == "https://example.ca/gtfs-rt/GTFS_ServiceAlerts.pb"
    assert s.guessed_feeds == {s.vehicle_positions_url, s.alerts_url}


def test_unguessable_sibling_feeds_are_off_and_explicit_values_win(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("GTFS_TRIP_UPDATES_URL", "https://example.ca/rt/trips")
    monkeypatch.setenv("GTFS_ALERTS_URL", "https://example.ca/rt/alerts")
    s = Settings.from_env()
    assert s.vehicle_positions_url == ""
    assert s.alerts_url == "https://example.ca/rt/alerts"
    assert s.guessed_feeds == frozenset()


def test_empty_setting_turns_a_feed_off_instead_of_guessing(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("GTFS_REALTIME_URL", "https://example.ca/gtfs-rt/GTFS_TripUpdates.pb")
    monkeypatch.setenv("GTFS_ALERTS_URL", "")
    s = Settings.from_env()
    assert s.alerts_url == ""
    assert s.guessed_feeds == {"https://example.ca/gtfs-rt/GTFS_VehiclePositions.pb"}
