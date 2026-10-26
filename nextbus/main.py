"""Web app: pages, the departure board fragment, and a small JSON API."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .arrivals import Board, Departure, build_board
from .config import Settings
from .gtfs_static import StaticFeed, StaticFeedManager, Stop
from .realtime import RealtimePoller

log = logging.getLogger("nextbus")

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
STATIC_RETRY_SECONDS = 300


def _asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    asset_version = _asset_version()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "next-bus (+https://github.com/justinh-rahb/next-bus)"},
        )
        static = StaticFeedManager(settings.static_url, settings.data_dir, settings.timezone)
        poller = RealtimePoller(
            client,
            settings.trip_updates_url,
            settings.vehicle_positions_url,
            settings.alerts_url,
            poll_seconds=settings.realtime_poll_seconds,
            idle_seconds=settings.realtime_idle_seconds,
            stale_seconds=settings.realtime_stale_seconds,
        )
        app.state.static = static
        app.state.poller = poller

        async def refresh_static() -> None:
            try:
                await static.refresh(client)
            except Exception:
                log.exception("Static GTFS refresh failed")

        async def static_loop() -> None:
            if built_now and static.feed:
                await asyncio.sleep(settings.static_refresh_hours * 3600)
            while True:
                await refresh_static()
                ok = static.feed is not None
                await asyncio.sleep(
                    settings.static_refresh_hours * 3600 if ok else STATIC_RETRY_SECONDS
                )

        built_now = not static.load_cached()
        if built_now:
            # Nothing to serve yet, so wait for the first build before taking traffic.
            await refresh_static()
        tasks = [asyncio.create_task(static_loop())]
        if poller.enabled:
            tasks.append(asyncio.create_task(poller.run()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await client.aclose()

    app = FastAPI(title="Next Bus", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    def feed_of(request: Request) -> StaticFeed:
        feed = request.app.state.static.feed
        if feed is None:
            raise HTTPException(503, "Schedule data is still loading. Try again in a minute.")
        return feed

    def clock(ts: int, feed: StaticFeed) -> str:
        return datetime.fromtimestamp(ts, feed.tz).strftime("%I:%M %p").lstrip("0")

    def countdown(ts: int, now: int) -> str:
        delta = ts - now
        if delta < 60:
            return "Now"
        if delta < 3600:
            return f"{delta // 60} min"
        return ""

    def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
        feed = request.app.state.static.feed
        return templates.TemplateResponse(
            request,
            name,
            {
                "settings": settings,
                "agency_name": settings.agency_name,
                "asset_version": asset_version,
                "kiosk": request.query_params.get("kiosk") in ("1", "true", "yes"),
                "clock": (lambda ts: clock(ts, feed)) if feed else str,
                "countdown": countdown,
                "tz": feed.tz.key if feed else "",
                **context,
            },
            status_code=status_code,
        )

    async def board_for(request: Request, stop_id: str) -> Board:
        feed = feed_of(request)
        stop = feed.stop(stop_id)
        if stop is None:
            raise HTTPException(404, "Unknown stop")
        poller: RealtimePoller = request.app.state.poller
        snapshot = await poller.get() if poller.enabled else poller.snapshot
        now = int(time.time())
        return await asyncio.to_thread(
            build_board,
            feed,
            snapshot,
            stop,
            now,
            realtime=poller.is_fresh(snapshot, now),
            limit=settings.max_arrivals,
            horizon_seconds=settings.horizon_minutes * 60,
            only_routes=set(filter(None, request.query_params.get("route", "").split(","))),
        )

    def stop_link(request: Request, stop: Stop) -> str:
        """Link to a stop page built only from known values, never raw query input.

        Only the stop page's own options are carried over: theme and kiosk become fixed
        strings, and routes are kept if they serve the stop.
        """
        params: dict[str, str] = {}
        q = request.query_params
        if q.get("theme") in ("light", "dark"):
            params["theme"] = "dark" if q["theme"] == "dark" else "light"
        if q.get("kiosk") in ("1", "true", "yes"):
            params["kiosk"] = "1"
        wanted = set(q.get("route", "").split(","))
        routes = [r.route_id for r in feed_of(request).routes_for_stop(stop.stop_id)]
        if kept := [r for r in routes if r in wanted]:
            params["route"] = ",".join(kept)
        query = f"?{urlencode(params)}" if params else ""
        return f"/stop/{quote(stop.stop_id, safe='')}{query}"

    def redirect_to_stop(request: Request, stop: Stop | None, status_code: int) -> Response:
        if stop is None:
            raise HTTPException(404, "Unknown stop")
        return RedirectResponse(stop_link(request, stop), status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return render(request, "error.html", exc.status_code, message=exc.detail)

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def home(request: Request, stop_id: str | None = None):
        if stop_id:
            # Links from the original app used /?stop_id=…
            return redirect_to_stop(request, feed_of(request).stop(stop_id), 301)
        return render(request, "home.html")

    @app.get("/next-bus")
    async def legacy_next_bus(request: Request, stop_id: str = ""):
        if not stop_id:
            return RedirectResponse("/", status_code=301)
        return redirect_to_stop(request, feed_of(request).stop(stop_id), 301)

    @app.get("/c/{code}")
    async def stop_by_code(request: Request, code: str):
        feed = feed_of(request)
        stops = feed.stops_by_code(code)
        if not stops:
            raise HTTPException(404, f"No stop with number {code}")
        if len(stops) == 1:
            return redirect_to_stop(request, stops[0], 302)
        # The same number is on more than one sign, so let the rider pick.
        choices = [(s, stop_link(request, s), feed.routes_for_stop(s.stop_id)) for s in stops]
        return render(request, "choose_stop.html", code=code, choices=choices)

    @app.get("/stop/{stop_id}", response_class=HTMLResponse)
    async def stop_page(request: Request, stop_id: str):
        board = await board_for(request, stop_id)
        # Saved to favourites/recents in the browser, so the home page can list it offline.
        stop_data = {
            "stop_id": board.stop.stop_id,
            "name": board.stop.name,
            "code": board.stop.code,
            "routes": [
                {"label": r.label, "color": r.color, "text_color": r.text_color}
                for r in board.routes
            ],
        }
        return render(request, "stop.html", board=board, stop_data=stop_data)

    @app.get("/stop/{stop_id}/board", response_class=HTMLResponse)
    async def stop_board(request: Request, stop_id: str):
        board = await board_for(request, stop_id)
        response = render(request, "_board.html", board=board)
        response.headers["Cache-Control"] = "no-store"
        return response

    def stop_json(stop, feed: StaticFeed, **extra) -> dict:
        return {
            "stop_id": stop.stop_id,
            "code": stop.code,
            "name": stop.name,
            "lat": stop.lat,
            "lon": stop.lon,
            "routes": [
                {"id": r.route_id, "label": r.label, "color": r.color, "text_color": r.text_color}
                for r in feed.routes_for_stop(stop.stop_id)
            ],
            **extra,
        }

    @app.get("/api/search")
    async def api_search(request: Request, q: str = Query("", max_length=80)):
        feed = feed_of(request)
        return [stop_json(s, feed) for s in feed.search(q)]

    @app.get("/api/nearby")
    async def api_nearby(
        request: Request,
        lat: float = Query(ge=-90, le=90),
        lon: float = Query(ge=-180, le=180),
    ):
        feed = feed_of(request)
        return [stop_json(s, feed, distance_m=round(d)) for s, d in feed.nearby(lat, lon)]

    def departure_json(d: Departure, feed: StaticFeed) -> dict:
        return {
            "trip_id": d.trip_id,
            "route": {
                "id": d.route.route_id,
                "badge": d.badge,
                "label": d.route.label,
                "color": d.route.color,
                "text_color": d.route.text_color,
            },
            "headsign": d.headsign,
            "status": d.status,
            "scheduled": datetime.fromtimestamp(d.scheduled, feed.tz).isoformat(),
            "predicted": (
                datetime.fromtimestamp(d.predicted, feed.tz).isoformat() if d.predicted else None
            ),
            "delay_minutes": d.delay_minutes,
            "stops_away": d.stops_away,
            "vehicle": d.vehicle or None,
            "occupancy": d.occupancy,
        }

    @app.get("/api/stops/{stop_id}/departures")
    async def api_departures(request: Request, stop_id: str):
        board = await board_for(request, stop_id)
        feed = feed_of(request)
        return {
            "stop": stop_json(board.stop, feed),
            "realtime": board.realtime,
            "generated_at": datetime.fromtimestamp(board.now, feed.tz).isoformat(),
            "departures": [departure_json(d, feed) for d in board.departures],
            "alerts": [
                {
                    "header": a.header,
                    "description": a.description,
                    "effect": a.effect,
                    "scope": scope,
                    "url": a.url or None,
                }
                for a, scope in board.alerts
            ],
        }

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def healthz(request: Request):
        static = request.app.state.static
        poller: RealtimePoller = request.app.state.poller
        snapshot = poller.snapshot
        body = {
            "static": static.feed.meta.get("feed_version") if static.feed else None,
            "realtime_fresh": poller.is_fresh(snapshot),
            "realtime_feed_time": snapshot.feed_time,
        }
        return JSONResponse(body, status_code=200 if static.feed else 503)

    @app.get("/manifest.webmanifest")
    async def manifest():
        name = f"Next Bus · {settings.agency_name}" if settings.agency_name else "Next Bus"
        return JSONResponse(
            {
                "name": name,
                "short_name": "Next Bus",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#0f1115",
                "theme_color": "#0f1115",
                "icons": [
                    {
                        "src": "/static/icons/icon-192x192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                    },
                    {
                        "src": "/static/icons/icon-512x512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                    },
                ],
            },
            media_type="application/manifest+json",
        )

    # Served from the root so it controls the whole site. The original app registered a
    # worker at this same path, so installed copies pick up the new one.
    sw_source = (STATIC_DIR / "service-worker.js").read_text()

    @app.get("/service-worker.js")
    async def service_worker():
        return Response(
            sw_source.replace("__VERSION__", asset_version),
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/offline", response_class=HTMLResponse)
    async def offline(request: Request):
        return render(request, "error.html", message="You're offline. Showing what we have.")

    return app


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
