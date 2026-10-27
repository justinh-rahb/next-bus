# Next Bus

Live bus departures for any stop, built on an agency's open GTFS and GTFS-Realtime feeds. It runs at [nextbus.oncornerstone.app](https://nextbus.oncornerstone.app) for the Hamilton Street Railway (HSR), but works with any agency that publishes GTFS.

## Features

- **Find a stop** by name ("king james") or by the number on the stop sign, or list the stops near you.
- **Favourites and recent stops** are saved in your browser, with no account needed. Each favourite shows its next three buses, updated live.
- **Live predictions** come from GTFS-Realtime and show how late or early the bus is and how many stops away it is. Cancelled trips and skipped stops are marked.
- **Correct schedules**: the service calendar is applied (weekday, weekend and holiday service), trips that run past midnight are handled, and a bus that already has a live prediction isn't listed a second time as scheduled.
- **Service alerts** for the stop and the routes that serve it.
- **Route filter**: tap route chips to show only the buses you care about. The filter is kept in the URL, so it can be bookmarked.
- **Kiosk mode** (`?kiosk=1`) is a large, auto-refreshing board for wall-mounted screens, with a clock and the current weather at the stop.
- **Installable** as a home-screen app, with light and dark themes. It works without any CDN.

## URLs

| URL | What it is |
| --- | --- |
| `/` | Search, nearby stops, favourites |
| `/stop/<stop_id>` | Departure board. Add `?route=<route_id>,…`, `?kiosk=1` or `?theme=dark` |
| `/c/<stop_code>` | Redirects to a stop by the number printed on its sign, handy for QR codes |
| `/?stop_id=<stop_id>` | Old-style link, redirects to `/stop/<stop_id>` |
| `/api/search?q=` | Stop search (JSON) |
| `/api/nearby?lat=&lon=` | Stops within 800 m (JSON) |
| `/api/stops/<stop_id>/departures` | Departures, alerts and realtime status (JSON) |
| `/api/next?stops=<id>,<id>` | Next few departures for up to 20 stops (JSON) |
| `/api/docs` | Interactive API docs |
| `/healthz` | 200 once the schedule is loaded |

## How it works

- **Static schedule.** On startup, the GTFS zip is downloaded and indexed into SQLite under `DATA_DIR` (about 3 seconds for HSR). The app checks for a new feed every few hours using `ETag`/`If-Modified-Since`, rebuilds in the background when it changes, and swaps the new index in without downtime.
- **Realtime.** Trip updates, vehicle positions and alerts are fetched together every 20 seconds and shared across all requests. Polling stops when nobody has looked at a stop for 5 minutes. If the agency's feed is slow or down, the board falls back to scheduled times and says so.
- **Weather.** Kiosk weather comes from [Open-Meteo](https://open-meteo.com/), which needs no API key. The server fetches it and caches it for 10 minutes per square kilometre, so many screens share one request.
- **Frontend.** Pages are rendered on the server. A small script (`nextbus/static/app.js`) refreshes the board, counts down every second, and handles search and favourites.

## Running locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run uvicorn nextbus.main:create_app --factory --reload
```

Open http://localhost:8000. Run the tests and linter with:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Configuration

All settings are environment variables and all are optional. The defaults point at HSR.

| Variable | Default | |
| --- | --- | --- |
| `GTFS_STATIC_URL` | HSR `google_transit.zip` | URL or local path to a GTFS zip |
| `GTFS_TRIP_UPDATES_URL` | HSR trip updates | `GTFS_REALTIME_URL` is also accepted. Set it empty to disable realtime |
| `GTFS_VEHICLE_POSITIONS_URL` | Next to the trip updates feed | Set it empty to disable |
| `GTFS_ALERTS_URL` | Next to the trip updates feed | Set it empty to disable |
| `WEATHER_URL` | Open-Meteo forecast API | Set it empty to turn off kiosk weather |
| `AGENCY_NAME` | | Shown in the header and title |
| `AGENCY_LOGO_URL` | Bus icon | For example `/static/icons/hsr-logo.png` |
| `AGENCY_TIMEZONE` | From `agency.txt` | |
| `DATA_DIR` | `data` | Where the schedule index is cached |
| `MAX_ARRIVALS` | `10` | Departures shown per stop |
| `HORIZON_MINUTES` | `180` | How far ahead to look |
| `REALTIME_POLL_SECONDS` | `20` | |
| `REALTIME_IDLE_SECONDS` | `300` | Stop polling after this long without visitors |
| `REALTIME_STALE_SECONDS` | `180` | Realtime data older than this is ignored |
| `STATIC_REFRESH_HOURS` | `6` | |
| `LOG_LEVEL` | `INFO` | |

For another agency, setting `GTFS_STATIC_URL` and `GTFS_REALTIME_URL` is usually enough. If the trip updates URL contains `TripUpdates`, the vehicle positions and alerts feeds are assumed to sit next to it (`…VehiclePositions…`, `…ServiceAlerts…`). Otherwise set them explicitly.

If the agency's schedule file doesn't cover today (some publish next season's timetable early), the logs warn and stop pages say when the schedule starts or ended.

## Deploying

Build the `Dockerfile` and expose port 8000 (override with `PORT`). Run one container with one process: the schedule index and realtime cache live in memory and are shared by every request.

Mounting a volume at `/app/data` lets restarts reuse the schedule index instead of rebuilding it. This is optional.

The image includes a `HEALTHCHECK` against `/healthz`. In Coolify, use the **Dockerfile** build pack and either rely on that health check or point Coolify's own check at `/healthz` on port 8000.

## License

[MIT](LICENSE)
