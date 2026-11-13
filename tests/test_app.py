import pytest
from fastapi.testclient import TestClient

from nextbus.config import Settings
from nextbus.main import create_app


@pytest.fixture(scope="module")
def client(gtfs_zip, tmp_path_factory):
    settings = Settings(
        static_url=str(gtfs_zip),
        trip_updates_url="",
        vehicle_positions_url="",
        alerts_url="",
        weather_url="",
        agency_name="TT",
        data_dir=tmp_path_factory.mktemp("data"),
    )
    with TestClient(create_app(settings), follow_redirects=False) as c:
        yield c


def test_home(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Next Bus" in r.text and "TT" in r.text


def test_legacy_stop_links_redirect(client):
    r = client.get("/?stop_id=S1&theme=dark")
    assert r.status_code == 301
    assert r.headers["location"] == "/stop/S1?theme=dark"
    assert client.get("/next-bus?stop_id=S1").headers["location"] == "/stop/S1"


def test_legacy_redirects_only_go_to_known_stops(client):
    for evil in ("//evil.example", "../admin", "https://evil.example"):
        assert client.get("/", params={"stop_id": evil}).status_code == 404
        assert client.get("/next-bus", params={"stop_id": evil}).status_code == 404


def test_redirects_keep_only_known_options(client):
    r = client.get(
        "/", params={"stop_id": "S1", "theme": "dark", "kiosk": "1", "route": "R5,R9", "x": "//e"}
    )
    assert r.headers["location"] == "/stop/S1?theme=dark&kiosk=1&route=R5"
    r = client.get("/c/1001", params={"theme": "//evil", "route": "R1"})
    assert r.headers["location"] == "/stop/S1?route=R1"


def test_stop_code_redirect(client):
    r = client.get("/c/1001")
    assert r.status_code == 302
    assert r.headers["location"] == "/stop/S1"
    assert client.get("/c/9999").status_code == 404


def test_shared_stop_code_shows_chooser(client):
    r = client.get("/c/2002", params={"theme": "dark", "x": "//evil"})
    assert r.status_code == 200
    assert "Which stop 2002?" in r.text
    assert 'href="/stop/S3?theme=dark"' in r.text
    assert 'href="/stop/S4?theme=dark"' in r.text
    assert "evil" not in r.text


def test_stop_page(client):
    r = client.get("/stop/S1")
    assert r.status_code == 200
    assert "KING at JAMES" in r.text
    assert "Stop 1001" in r.text
    # Realtime is disabled, so the page must say so.
    assert "Live tracking unavailable" in r.text


def test_stop_board_fragment(client):
    r = client.get("/stop/S1/board")
    assert r.status_code == 200
    assert r.text.lstrip().startswith('<div class="board"')
    assert r.headers["cache-control"] == "no-store"


def test_unknown_stop(client):
    assert client.get("/stop/nope").status_code == 404
    r = client.get("/api/stops/nope/departures")
    assert r.status_code == 404
    assert r.json() == {"error": "Unknown stop"}


def test_api_search(client):
    results = client.get("/api/search", params={"q": "king"}).json()
    assert results[0]["stop_id"] == "S1"
    assert [r["label"] for r in results[0]["routes"]] == ["1", "5"]


def test_api_nearby(client):
    results = client.get("/api/nearby", params={"lat": 43.2571, "lon": -79.8689}).json()
    assert {r["stop_id"] for r in results} >= {"S1", "S2"}
    assert client.get("/api/nearby", params={"lat": 200, "lon": 0}).status_code == 422


def test_api_departures_shape(client):
    body = client.get("/api/stops/S1/departures").json()
    assert body["stop"]["name"] == "KING at JAMES"
    assert body["realtime"] is False
    assert isinstance(body["departures"], list)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["static"] == "test-1"


def test_service_worker_is_versioned(client):
    r = client.get("/service-worker.js")
    assert "__VERSION__" not in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_manifest(client):
    assert client.get("/manifest.webmanifest").json()["name"] == "Next Bus · TT"


def test_api_next_for_favourites(client):
    body = client.get("/api/next", params={"stops": "S1,nope,S1,S2", "n": 2}).json()
    assert set(body["stops"]) == {"S1", "S2"}
    assert body["tz"] == "America/Toronto"
    assert all(len(deps) <= 2 for deps in body["stops"].values())


def test_weather_fragment_is_empty_when_disabled(client):
    r = client.get("/stop/S1/weather")
    assert r.status_code == 200
    assert "weather" not in r.text
    assert 'id="kiosk-weather"' in client.get("/stop/S1?kiosk=1").text
    assert 'id="kiosk-weather"' not in client.get("/stop/S1").text


def test_trip_page(client):
    r = client.get("/trip/T1")
    assert r.status_code == 200
    assert "WEST LOOP" in r.text
    assert 'data-src="/trip/T1/board"' in r.text
    assert client.get("/trip/nope").status_code == 404
    board = client.get("/trip/T1/board")
    assert board.text.lstrip().startswith('<div class="trip-board"')
    assert board.headers["cache-control"] == "no-store"


def test_bus_page_waits_for_a_bus_that_is_not_running(client):
    # Realtime is off in tests, so no bus is ever found, but a mounted screen keeps polling.
    r = client.get("/bus/2313")
    assert r.status_code == 200
    assert "isn't running a trip right now" in r.text
    assert 'data-src="/bus/2313/board"' in r.text


def test_bus_finder(client):
    assert "Which bus are you on?" in client.get("/bus").text
    r = client.get("/bus", params={"n": "2313"})
    assert r.status_code == 404
    assert "No bus numbered 2313" in r.text


def test_api_trip_and_buses(client):
    body = client.get("/api/trips/T1").json()
    assert body["route"]["badge"] == "1A"
    assert [s["stop_id"] for s in body["stops"]] == ["S1", "S2", "S3"]
    assert client.get("/api/buses/2313").status_code == 404
    nearby = client.get("/api/buses/nearby", params={"lat": 43.25, "lon": -79.87}).json()
    assert nearby == {"realtime": False, "buses": []}


def test_bus_and_trip_weather(client):
    # Weather is off in tests, so the fragments are empty, but the kiosk screens ask for them.
    for base in ("/trip/T1", "/bus/2313"):
        assert "weather" not in client.get(f"{base}/weather").text
        assert f'data-src="{base}/weather"' in client.get(f"{base}?kiosk=1").text
        assert 'id="kiosk-weather"' not in client.get(base).text
    assert client.get("/trip/nope/weather").status_code == 404
