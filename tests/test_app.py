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


def test_legacy_redirects_cannot_leave_the_stop_path(client):
    for evil in ("//evil.example", "../admin", "x?y=1#z", "https://evil.example"):
        location = client.get("/", params={"stop_id": evil}).headers["location"]
        assert location.startswith("/stop/")
        assert "/" not in location.removeprefix("/stop/")
        assert client.get("/next-bus", params={"stop_id": evil}).headers["location"] == location


def test_stop_code_redirect(client):
    r = client.get("/c/1001")
    assert r.status_code == 302
    assert r.headers["location"] == "/stop/S1"
    assert client.get("/c/9999").status_code == 404


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
