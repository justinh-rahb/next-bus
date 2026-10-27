import asyncio

import httpx

from nextbus.weather import WeatherService, parse

SAMPLE = {
    "current": {
        "temperature_2m": 11.1,
        "apparent_temperature": 7.6,
        "weather_code": 61,
        "wind_speed_10m": 13.1,
        "is_day": 1,
    },
    "daily": {
        "temperature_2m_max": [11.6],
        "temperature_2m_min": [4.8],
        "precipitation_probability_max": [40],
    },
}


def test_parse():
    w = parse(SAMPLE)
    assert (w.temperature, w.feels_like, w.label, w.icon) == (11, 8, "Light rain", "🌦️")
    assert (w.high, w.low, w.precipitation_chance, w.wind_kmh) == (12, 5, 40, 13)


def test_parse_night_and_unknown_code():
    night = parse({"current": {**SAMPLE["current"], "weather_code": 0, "is_day": 0}})
    assert night.icon == "🌙"
    assert night.high is None
    assert parse({"current": {"temperature_2m": 1, "weather_code": 1234}}).label == ""


def test_service_caches_by_area_and_survives_errors():
    calls = []
    fail = False

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["latitude"])
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json=SAMPLE)

    async def run():
        nonlocal fail
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = WeatherService(client, "https://weather.test/v1/forecast")
            first = await service.current(43.2571, -79.8689)
            # A stop a few metres away shares the cached reading.
            assert await service.current(43.2572, -79.8688) == first
            assert len(calls) == 1
            # Once expired, a failed refresh falls back to the old reading.
            service.ttl = 0
            fail = True
            assert await service.current(43.2571, -79.8689) == first
            assert len(calls) == 2
            assert await service.current(None, None) is None
            assert await WeatherService(client, "").current(43.0, -79.0) is None

    asyncio.run(run())
