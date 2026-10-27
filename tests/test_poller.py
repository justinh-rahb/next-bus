import asyncio
import logging

import httpx
from google.transit import gtfs_realtime_pb2 as pb

from nextbus.realtime import RealtimePoller

TRIPS = "https://rt.test/TripUpdates.pb"
VEHICLES = "https://rt.test/VehiclePositions.pb"
ALERTS = "https://rt.test/ServiceAlerts.pb"


def empty_feed() -> bytes:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    return feed.SerializeToString()


def poll(statuses: dict[str, int], guessed=(), rounds=3):
    """Refresh a poller `rounds` times; returns (poller, request counts per URL)."""
    hits: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        hits[url] = hits.get(url, 0) + 1
        status = statuses.get(url, 200)
        return httpx.Response(status, content=empty_feed() if status == 200 else b"")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            poller = RealtimePoller(client, TRIPS, VEHICLES, ALERTS, guessed=guessed)
            for _ in range(rounds):
                await poller.refresh()
            return poller

    return asyncio.run(run()), hits


def test_missing_guessed_feed_is_turned_off_with_one_warning(caplog):
    caplog.set_level(logging.WARNING)
    poller, hits = poll({VEHICLES: 404}, guessed={VEHICLES, ALERTS})
    assert hits[VEHICLES] == 1
    assert poller.urls[1] == ""
    # The guessed alerts feed exists, so it stays on.
    assert hits[ALERTS] == 3
    assert poller.urls[2] == ALERTS
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1
    assert "GTFS_VEHICLE_POSITIONS_URL" in warnings[0]


def test_guessed_feed_survives_temporary_outages():
    poller, hits = poll({VEHICLES: 503}, guessed={VEHICLES})
    assert hits[VEHICLES] == 3
    assert poller.urls[1] == VEHICLES


def test_configured_feed_is_never_turned_off():
    poller, hits = poll({VEHICLES: 404})
    assert hits[VEHICLES] == 3
    assert poller.urls[1] == VEHICLES


def test_guess_that_worked_once_is_treated_as_configured():
    poller, _ = poll({}, guessed={VEHICLES}, rounds=1)
    # Later 404s are then real outages, reported rather than silenced.
    assert VEHICLES not in poller.guessed
    assert poller.urls[1] == VEHICLES
