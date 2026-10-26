FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY nextbus ./nextbus
RUN mkdir -p /app/data && chown app /app/data
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    PORT=8000
USER app
EXPOSE 8000
# Optional: mount a volume at /app/data so restarts skip re-indexing the schedule.
VOLUME /app/data
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"]
# One process on purpose: the realtime poller and schedule index are shared in memory.
CMD ["sh", "-c", "exec uvicorn nextbus.main:create_app --factory --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
