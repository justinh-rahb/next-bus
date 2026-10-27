"""Current conditions for a stop's location, from Open-Meteo (no API key needed).

Results are cached per ~1 km grid square, so a wall of kiosks makes one request every few
minutes rather than one each.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

# WMO weather interpretation codes, as used by Open-Meteo: (label, day icon, night icon).
CODES: dict[int, tuple[str, str, str]] = {
    0: ("Clear", "☀️", "🌙"),
    1: ("Mainly clear", "🌤️", "🌙"),
    2: ("Partly cloudy", "⛅", "☁️"),
    3: ("Overcast", "☁️", "☁️"),
    45: ("Fog", "🌫️", "🌫️"),
    48: ("Freezing fog", "🌫️", "🌫️"),
    51: ("Light drizzle", "🌦️", "🌧️"),
    53: ("Drizzle", "🌦️", "🌧️"),
    55: ("Heavy drizzle", "🌧️", "🌧️"),
    56: ("Freezing drizzle", "🌧️", "🌧️"),
    57: ("Freezing drizzle", "🌧️", "🌧️"),
    61: ("Light rain", "🌦️", "🌧️"),
    63: ("Rain", "🌧️", "🌧️"),
    65: ("Heavy rain", "🌧️", "🌧️"),
    66: ("Freezing rain", "🌧️", "🌧️"),
    67: ("Freezing rain", "🌧️", "🌧️"),
    71: ("Light snow", "🌨️", "🌨️"),
    73: ("Snow", "🌨️", "🌨️"),
    75: ("Heavy snow", "❄️", "❄️"),
    77: ("Snow grains", "🌨️", "🌨️"),
    80: ("Rain showers", "🌦️", "🌧️"),
    81: ("Rain showers", "🌧️", "🌧️"),
    82: ("Heavy showers", "🌧️", "🌧️"),
    85: ("Snow showers", "🌨️", "🌨️"),
    86: ("Heavy snow showers", "❄️", "❄️"),
    95: ("Thunderstorm", "⛈️", "⛈️"),
    96: ("Thunderstorm, hail", "⛈️", "⛈️"),
    99: ("Thunderstorm, hail", "⛈️", "⛈️"),
}


@dataclass(frozen=True, slots=True)
class Weather:
    temperature: int
    feels_like: int
    label: str
    icon: str
    high: int | None
    low: int | None
    precipitation_chance: int | None
    wind_kmh: int | None


def _round(value) -> int | None:
    return round(value) if isinstance(value, int | float) else None


def parse(data: dict) -> Weather:
    current = data["current"]
    daily = data.get("daily", {})
    label, day_icon, night_icon = CODES.get(int(current.get("weather_code", -1)), ("", "🌡️", "🌡️"))

    def today(key: str):
        values = daily.get(key) or [None]
        return _round(values[0])

    temperature = round(current["temperature_2m"])
    return Weather(
        temperature=temperature,
        feels_like=_round(current.get("apparent_temperature")) or temperature,
        label=label,
        icon=day_icon if current.get("is_day", 1) else night_icon,
        high=today("temperature_2m_max"),
        low=today("temperature_2m_min"),
        precipitation_chance=today("precipitation_probability_max"),
        wind_kmh=_round(current.get("wind_speed_10m")),
    )


class WeatherService:
    def __init__(self, client: httpx.AsyncClient, url: str, ttl_seconds: int = 600):
        self.client = client
        self.url = url
        self.ttl = ttl_seconds
        self._cache: dict[tuple[float, float], tuple[float, Weather]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def current(self, lat: float | None, lon: float | None) -> Weather | None:
        if not self.enabled or lat is None or lon is None:
            return None
        key = (round(lat, 2), round(lon, 2))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.ttl:
            return cached[1]
        try:
            response = await self.client.get(
                self.url,
                params={
                    "latitude": key[0],
                    "longitude": key[1],
                    "current": "temperature_2m,apparent_temperature,weather_code,"
                    "wind_speed_10m,is_day",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 1,
                    "timezone": "auto",
                },
                timeout=8,
            )
            response.raise_for_status()
            weather = parse(response.json())
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            log.warning("Weather lookup failed: %s", exc)
            # A slightly old reading beats an empty corner on a kiosk.
            return cached[1] if cached else None
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), weather)
        return weather
