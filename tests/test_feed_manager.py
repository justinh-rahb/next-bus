"""Switching between published timetables, including next season's published early."""

import asyncio
import io
import json
import os
import zipfile
from datetime import date

import pytest
from conftest import FIXTURE

from nextbus.gtfs_static import StaticFeedManager

NEXT_SEASON = {
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261102,20261231\n"
    ),
    "calendar_dates.txt": "service_id,date,exception_type\n",
    "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang,feed_version\n"
    "Test Transit,https://example.com,en,next-season\n",
}


def write_feed(path, overrides=None, stamp=1):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in FIXTURE.glob("*.txt"):
            zf.writestr(f.name, (overrides or {}).get(f.name, f.read_text()))
    path.write_bytes(buf.getvalue())
    # The manager notices local changes by mtime.
    os.utime(path, ns=(stamp * 10**9, stamp * 10**9))


class Clock:
    def __init__(self, day):
        self.day = day

    def __call__(self, feed):
        return self.day


def manager(tmp_path, zip_path, clock):
    m = StaticFeedManager(str(zip_path), tmp_path / "data")
    m.today = clock
    return m


def refresh(m):
    return asyncio.run(m.refresh(None))


def version(feed):
    return feed.meta["feed_version"] if feed else None


@pytest.fixture
def zip_path(tmp_path):
    path = tmp_path / "feed.zip"
    write_feed(path, stamp=1)
    return path


def test_next_season_is_held_until_it_starts(tmp_path, zip_path):
    clock = Clock(date(2026, 10, 27))
    m = manager(tmp_path, zip_path, clock)
    assert refresh(m)
    assert version(m.feed) == "test-1"

    write_feed(zip_path, NEXT_SEASON, stamp=2)
    assert refresh(m)
    assert version(m.feed) == "test-1"
    assert version(m.pending) == "next-season"
    # Nothing new upstream, so no rebuild.
    assert not refresh(m)

    clock.day = date(2026, 11, 2)
    assert version(m.feed) == "next-season"
    assert m.pending is None
    state = json.loads((tmp_path / "data" / "current.json").read_text())
    assert "pending" not in state


def test_pending_schedule_survives_a_restart(tmp_path, zip_path):
    clock = Clock(date(2026, 10, 27))
    m = manager(tmp_path, zip_path, clock)
    refresh(m)
    write_feed(zip_path, NEXT_SEASON, stamp=2)
    refresh(m)

    restarted = manager(tmp_path, zip_path, clock)
    assert restarted.load_cached()
    assert version(restarted.feed) == "test-1"
    assert version(restarted.pending) == "next-season"
    assert not refresh(restarted)

    clock.day = date(2026, 11, 2)
    assert version(restarted.feed) == "next-season"
    # Only the active index and the one it replaced are left on disk.
    assert len(list((tmp_path / "data").glob("gtfs-*.sqlite"))) <= 2


def test_fresh_install_uses_whatever_is_published(tmp_path, zip_path):
    write_feed(zip_path, NEXT_SEASON, stamp=2)
    m = manager(tmp_path, zip_path, Clock(date(2026, 10, 27)))
    refresh(m)
    assert version(m.feed) == "next-season"
    assert m.pending is None


def test_expired_schedule_is_replaced_immediately(tmp_path, zip_path):
    clock = Clock(date(2026, 10, 27))
    m = manager(tmp_path, zip_path, clock)
    refresh(m)
    # The current feed has run out (it ends 2026-12-31) before the new one starts.
    clock.day = date(2027, 1, 5)
    write_feed(
        zip_path,
        {**NEXT_SEASON, "calendar.txt": NEXT_SEASON["calendar.txt"].replace("2026", "2027")},
        stamp=2,
    )
    refresh(m)
    assert version(m.feed) == "next-season"
    assert m.pending is None
