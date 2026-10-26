import io
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nextbus.gtfs_static import StaticFeed, build_index

FIXTURE = Path(__file__).parent / "fixtures" / "gtfs"
TZ = ZoneInfo("America/Toronto")


def local_ts(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp())


def fixture_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in FIXTURE.glob("*.txt"):
            zf.write(path, path.name)
    return buf.getvalue()


@pytest.fixture(scope="session")
def gtfs_zip(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("gtfs") / "feed.zip"
    path.write_bytes(fixture_zip())
    return path


@pytest.fixture(scope="session")
def feed(tmp_path_factory) -> StaticFeed:
    path = tmp_path_factory.mktemp("index") / "gtfs.sqlite"
    build_index(fixture_zip(), path, {"url": "test"})
    return StaticFeed(path)
