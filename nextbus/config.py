"""Settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HSR_RT = "https://opendata.hamilton.ca/GTFS-RT"


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _sibling_feed(trip_updates_url: str, name: str) -> str:
    """Guess a sibling realtime feed, e.g. GTFS_TripUpdates.pb -> GTFS_VehiclePositions.pb."""
    return (
        trip_updates_url.replace("TripUpdates", name) if "TripUpdates" in trip_updates_url else ""
    )


@dataclass(frozen=True)
class Settings:
    static_url: str = "https://opendata.hamilton.ca/GTFS-Static/google_transit.zip"
    trip_updates_url: str = f"{HSR_RT}/GTFS_TripUpdates.pb"
    vehicle_positions_url: str = f"{HSR_RT}/GTFS_VehiclePositions.pb"
    alerts_url: str = f"{HSR_RT}/GTFS_ServiceAlerts.pb"

    # Open-Meteo forecast endpoint for the kiosk weather display. Empty disables it.
    weather_url: str = "https://api.open-meteo.com/v1/forecast"

    agency_name: str = ""
    agency_logo_url: str = ""
    # Falls back to agency.txt when empty.
    timezone: str = ""

    data_dir: Path = field(default_factory=lambda: Path("data"))
    # How often to poll realtime feeds while someone is looking at a stop.
    realtime_poll_seconds: int = 20
    # Stop polling after this long without a request.
    realtime_idle_seconds: int = 300
    # Treat realtime data older than this as unavailable.
    realtime_stale_seconds: int = 180
    static_refresh_hours: int = 6

    max_arrivals: int = 10
    horizon_minutes: int = 180

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        # GTFS_REALTIME_URL is the name the original app used for trip updates.
        trip_updates = _str("GTFS_TRIP_UPDATES_URL", _str("GTFS_REALTIME_URL", d.trip_updates_url))
        # The HSR vehicle and alert feeds are only the default for the HSR trip feed; for
        # another agency, look next to its trip updates feed instead.
        hsr = trip_updates == d.trip_updates_url
        return cls(
            static_url=_str("GTFS_STATIC_URL", d.static_url),
            trip_updates_url=trip_updates,
            vehicle_positions_url=_str(
                "GTFS_VEHICLE_POSITIONS_URL",
                d.vehicle_positions_url if hsr else _sibling_feed(trip_updates, "VehiclePositions"),
            ),
            alerts_url=_str(
                "GTFS_ALERTS_URL",
                d.alerts_url if hsr else _sibling_feed(trip_updates, "ServiceAlerts"),
            ),
            weather_url=_str("WEATHER_URL", d.weather_url),
            agency_name=_str("AGENCY_NAME"),
            agency_logo_url=_str("AGENCY_LOGO_URL"),
            timezone=_str("AGENCY_TIMEZONE"),
            data_dir=Path(_str("DATA_DIR", "data")),
            realtime_poll_seconds=_int("REALTIME_POLL_SECONDS", d.realtime_poll_seconds),
            realtime_idle_seconds=_int("REALTIME_IDLE_SECONDS", d.realtime_idle_seconds),
            realtime_stale_seconds=_int("REALTIME_STALE_SECONDS", d.realtime_stale_seconds),
            static_refresh_hours=_int("STATIC_REFRESH_HOURS", d.static_refresh_hours),
            max_arrivals=_int("MAX_ARRIVALS", d.max_arrivals),
            horizon_minutes=_int("HORIZON_MINUTES", d.horizon_minutes),
        )
