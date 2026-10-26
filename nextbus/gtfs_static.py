"""Static GTFS: download the feed, index it into SQLite, and answer schedule queries.

The index is a throwaway cache. It is rebuilt whenever the upstream zip changes, written to a
new file, and swapped in atomically so requests never see a half-built database.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import re
import sqlite3
import time
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE stops (
    stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT, stop_desc TEXT,
    lat REAL, lon REAL, location_type INTEGER
);
CREATE TABLE routes (
    route_id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT, color TEXT, text_color TEXT
);
CREATE TABLE trips (
    trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, headsign TEXT, direction_id INTEGER
);
CREATE TABLE stop_times (
    trip_id TEXT, stop_id TEXT, stop_sequence INTEGER, departure INTEGER, pickup_type INTEGER
);
CREATE TABLE calendar (service_id TEXT PRIMARY KEY, days TEXT, start_date TEXT, end_date TEXT);
CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INTEGER);
"""

INDEXES = """
CREATE INDEX stop_times_stop ON stop_times (stop_id, departure);
CREATE INDEX stop_times_trip ON stop_times (trip_id, stop_sequence);
CREATE INDEX stops_code ON stops (stop_code);
CREATE INDEX stops_lat ON stops (lat);
CREATE INDEX calendar_dates_date ON calendar_dates (date);
-- The last stop of a trip is drop-off only, even when the feed doesn't say so.
UPDATE stop_times SET pickup_type = 1 WHERE (trip_id, stop_sequence) IN (
    SELECT trip_id, MAX(stop_sequence) FROM stop_times GROUP BY trip_id
);
CREATE TABLE stop_routes (stop_id TEXT, route_id TEXT, PRIMARY KEY (stop_id, route_id))
    WITHOUT ROWID;
INSERT INTO stop_routes
    SELECT DISTINCT st.stop_id, t.route_id FROM stop_times st JOIN trips t USING (trip_id);
"""


@dataclass(frozen=True, slots=True)
class Stop:
    stop_id: str
    code: str
    name: str
    desc: str
    lat: float | None
    lon: float | None


@dataclass(frozen=True, slots=True)
class Route:
    route_id: str
    short_name: str
    long_name: str
    color: str
    text_color: str

    @property
    def label(self) -> str:
        # HSR pads route numbers ("05"); riders say "5".
        name = self.short_name or self.long_name or self.route_id
        return name.lstrip("0") or name

    @property
    def css_color(self) -> str:
        return f"#{self.color}" if re.fullmatch(r"[0-9A-Fa-f]{6}", self.color) else ""

    @property
    def css_text_color(self) -> str:
        return f"#{self.text_color}" if re.fullmatch(r"[0-9A-Fa-f]{6}", self.text_color) else ""


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    trip_id: str
    route_id: str
    headsign: str
    stop_sequence: int
    service_date: date
    # Scheduled departure, seconds since the epoch.
    scheduled: int


def parse_gtfs_time(value: str) -> int | None:
    """'25:10:00' -> seconds after the start of the service day. Tolerates ' 4:30:00'."""
    value = value.strip()
    if not value:
        return None
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def service_day_start(day: date, tz: ZoneInfo) -> int:
    """Epoch seconds that GTFS times on `day` are measured from.

    The spec defines this as noon minus 12 hours, which differs from midnight on the days
    clocks change.
    """
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    return int(noon.timestamp()) - 12 * 3600


def natural_key(text: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text))


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    if name not in zf.namelist():
        return
    with zf.open(name) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            yield {k.strip(): (v or "").strip() for k, v in row.items() if k}


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def build_index(zip_bytes: bytes, path: Path, meta: dict[str, str]) -> None:
    """Write a fresh SQLite index for a GTFS zip to `path`."""
    started = time.monotonic()
    path.unlink(missing_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf, closing(sqlite3.connect(path)) as db:
        db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)

        agency = next(_rows(zf, "agency.txt"), {})
        meta = {**meta, "timezone": agency.get("agency_timezone", "")}
        feed_info = next(_rows(zf, "feed_info.txt"), {})
        for key in ("feed_version", "feed_start_date", "feed_end_date"):
            meta[key] = feed_info.get(key, "")
        db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())

        db.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    r["stop_id"],
                    r.get("stop_code", ""),
                    r.get("stop_name", ""),
                    r.get("stop_desc", ""),
                    _float(r.get("stop_lat", "")),
                    _float(r.get("stop_lon", "")),
                    _int(r.get("location_type", "")),
                )
                for r in _rows(zf, "stops.txt")
            ),
        )
        db.executemany(
            "INSERT INTO routes VALUES (?, ?, ?, ?, ?)",
            (
                (
                    r["route_id"],
                    r.get("route_short_name", ""),
                    r.get("route_long_name", ""),
                    r.get("route_color", ""),
                    r.get("route_text_color", ""),
                )
                for r in _rows(zf, "routes.txt")
            ),
        )
        db.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?)",
            (
                (
                    r["trip_id"],
                    r["route_id"],
                    r["service_id"],
                    r.get("trip_headsign", ""),
                    _int(r.get("direction_id", ""), -1),
                )
                for r in _rows(zf, "trips.txt")
            ),
        )
        db.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            (
                (
                    r["trip_id"],
                    r["stop_id"],
                    int(r["stop_sequence"]),
                    parse_gtfs_time(r.get("departure_time", "") or r.get("arrival_time", "")),
                    _int(r.get("pickup_type", "")),
                )
                for r in _rows(zf, "stop_times.txt")
            ),
        )
        days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        db.executemany(
            "INSERT INTO calendar VALUES (?, ?, ?, ?)",
            (
                (
                    r["service_id"],
                    "".join(r.get(d, "0") or "0" for d in days),
                    r["start_date"],
                    r["end_date"],
                )
                for r in _rows(zf, "calendar.txt")
            ),
        )
        db.executemany(
            "INSERT INTO calendar_dates VALUES (?, ?, ?)",
            (
                (r["service_id"], r["date"], int(r["exception_type"]))
                for r in _rows(zf, "calendar_dates.txt")
            ),
        )
        db.executescript(INDEXES)
        db.commit()
    log.info("Indexed static GTFS in %.1fs", time.monotonic() - started)


class StaticFeed:
    """Read-only queries over an index built by `build_index`."""

    def __init__(self, path: Path, timezone: str = ""):
        self.path = path
        self.meta = dict(self._query("SELECT key, value FROM meta"))
        self.tz = ZoneInfo(timezone or self.meta.get("timezone") or "America/Toronto")
        self.routes = {r[0]: Route(*r) for r in self._query("SELECT * FROM routes")}
        self._services: dict[date, frozenset[str]] = {}

    def _query(self, sql: str, params: Iterable = ()) -> list[tuple]:
        # A connection per query keeps this safe across threads; opening one is cheap.
        uri = f"file:{self.path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            return db.execute(sql, tuple(params)).fetchall()

    def stop(self, stop_id: str) -> Stop | None:
        rows = self._query(
            "SELECT stop_id, stop_code, stop_name, stop_desc, lat, lon FROM stops"
            " WHERE stop_id = ?",
            (stop_id,),
        )
        return Stop(*rows[0]) if rows else None

    def stop_by_code(self, code: str) -> Stop | None:
        rows = self._query(
            "SELECT stop_id, stop_code, stop_name, stop_desc, lat, lon FROM stops"
            " WHERE stop_code = ? AND location_type = 0",
            (code,),
        )
        return Stop(*rows[0]) if rows else None

    def routes_for_stop(self, stop_id: str) -> list[Route]:
        rows = self._query("SELECT route_id FROM stop_routes WHERE stop_id = ?", (stop_id,))
        routes = [self.routes[r[0]] for r in rows if r[0] in self.routes]
        return sorted(routes, key=lambda r: natural_key(r.label))

    def search(self, query: str, limit: int = 15) -> list[Stop]:
        """Match stop codes exactly, otherwise every word of the query against stop names."""
        query = query.strip()
        if not query:
            return []
        cols = "stop_id, stop_code, stop_name, stop_desc, lat, lon"
        found: dict[str, Stop] = {}
        if query.isdigit():
            for row in self._query(
                f"SELECT {cols} FROM stops WHERE location_type = 0"
                " AND (stop_code = ? OR stop_id = ?) ORDER BY stop_code != ?",
                (query, query, query),
            ):
                found.setdefault(row[0], Stop(*row))
        words = [w for w in re.split(r"[\s&/,]+", query.upper()) if w and w not in {"AT", "AND"}]
        if words:
            where = " AND ".join("stop_name LIKE ?" for _ in words)
            params = [f"%{w}%" for w in words]
            rows = self._query(
                f"SELECT {cols} FROM stops WHERE location_type = 0 AND {where}"
                # Prefer names that start with the first word, then shorter names.
                " ORDER BY stop_name NOT LIKE ?, length(stop_name), stop_name LIMIT ?",
                (*params, f"{words[0]}%", limit),
            )
            for row in rows:
                found.setdefault(row[0], Stop(*row))
        return list(found.values())[:limit]

    def nearby(self, lat: float, lon: float, radius_m: float = 800, limit: int = 12):
        """Stops within `radius_m`, closest first, as (stop, metres) pairs."""
        dlat = radius_m / 111_320
        dlon = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.01))
        rows = self._query(
            "SELECT stop_id, stop_code, stop_name, stop_desc, lat, lon FROM stops"
            " WHERE location_type = 0 AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
        )
        out = []
        for row in rows:
            stop = Stop(*row)
            d = haversine_m(lat, lon, stop.lat, stop.lon)
            if d <= radius_m:
                out.append((stop, d))
        out.sort(key=lambda pair: pair[1])
        return out[:limit]

    def services_on(self, day: date) -> frozenset[str]:
        if day not in self._services:
            ymd = day.strftime("%Y%m%d")
            active = {
                r[0]
                for r in self._query(
                    "SELECT service_id FROM calendar WHERE start_date <= ? AND end_date >= ?"
                    " AND substr(days, ?, 1) = '1'",
                    (ymd, ymd, day.weekday() + 1),
                )
            }
            for service_id, exception in self._query(
                "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (ymd,)
            ):
                if exception == 1:
                    active.add(service_id)
                else:
                    active.discard(service_id)
            if len(self._services) > 16:
                self._services.clear()
            self._services[day] = frozenset(active)
        return self._services[day]

    def departures(self, stop_id: str, start: int, end: int) -> list[ScheduledStop]:
        """Scheduled departures from a stop between two epoch times, earliest first."""
        first = datetime.fromtimestamp(start, self.tz).date() - timedelta(days=1)
        last = datetime.fromtimestamp(end, self.tz).date()
        out: list[ScheduledStop] = []
        day = first
        while day <= last:
            base = service_day_start(day, self.tz)
            services = self.services_on(day)
            if services and end - base >= 0:
                rows = self._query(
                    "SELECT st.trip_id, t.route_id, t.headsign, st.stop_sequence, st.departure,"
                    " t.service_id FROM stop_times st JOIN trips t USING (trip_id)"
                    " WHERE st.stop_id = ? AND st.departure BETWEEN ? AND ?"
                    " AND st.pickup_type != 1",
                    (stop_id, start - base, end - base),
                )
                out.extend(
                    ScheduledStop(trip_id, route_id, headsign, seq, day, base + dep)
                    for trip_id, route_id, headsign, seq, dep, service_id in rows
                    if service_id in services
                )
            day += timedelta(days=1)
        out.sort(key=lambda s: s.scheduled)
        return out


def haversine_m(lat1: float, lon1: float, lat2: float | None, lon2: float | None) -> float:
    if lat2 is None or lon2 is None:
        return math.inf
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


class StaticFeedManager:
    """Keeps the current StaticFeed and replaces it when the upstream zip changes."""

    def __init__(self, url: str, data_dir: Path, timezone: str = ""):
        self.url = url
        self.data_dir = data_dir
        self.timezone = timezone
        self.feed: StaticFeed | None = None
        self.checked_at: float | None = None
        self._state_file = data_dir / "current.json"

    def load_cached(self) -> bool:
        """Use an index left by a previous run, if it was built from the same URL."""
        try:
            state = json.loads(self._state_file.read_text())
            path = self.data_dir / state["file"]
            if state.get("url") == self.url and path.exists():
                self.feed = StaticFeed(path, self.timezone)
                log.info("Using cached static GTFS %s", self.feed.meta.get("feed_version", ""))
                return True
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            log.info("No usable static GTFS cache: %s", exc)
        return False

    async def refresh(self, client: httpx.AsyncClient) -> bool:
        """Download the feed if it changed and swap in a new index. True if replaced."""
        self.checked_at = time.time()
        meta = self.feed.meta if self.feed else {}
        if "://" not in self.url or self.url.startswith("file://"):
            local = Path(self.url.removeprefix("file://"))
            content = local.read_bytes()
            etag, modified = str(local.stat().st_mtime_ns), ""
            if self.feed and meta.get("etag") == etag:
                return False
        else:
            headers = {}
            if self.feed and meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if self.feed and meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]
            response = await client.get(self.url, headers=headers, timeout=120)
            if response.status_code == 304:
                return False
            response.raise_for_status()
            content = response.content
            etag = response.headers.get("etag", "")
            modified = response.headers.get("last-modified", "")

        self.data_dir.mkdir(parents=True, exist_ok=True)
        name = f"gtfs-{int(time.time())}.sqlite"
        path = self.data_dir / name
        await asyncio.to_thread(
            build_index,
            content,
            path,
            {"url": self.url, "etag": etag, "last_modified": modified},
        )
        old = self.feed
        self.feed = StaticFeed(path, self.timezone)
        self._state_file.write_text(json.dumps({"url": self.url, "file": name}))
        # Keep the previous index for requests that are still using it.
        for stale in self.data_dir.glob("gtfs-*.sqlite"):
            if stale != path and (old is None or stale != old.path):
                stale.unlink(missing_ok=True)
        log.info("Loaded static GTFS %s", self.feed.meta.get("feed_version", ""))
        return True
