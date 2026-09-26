# syntax=docker/dockerfile:1.7
# Multi-target image for all Python services.
#   api        - FastAPI (no cloud SDKs: credential boundary)
#   connector  - Temporal worker with cloud SDKs
#   dev        - everything + dev tools, used for tests/lint in CI and locally

ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.18

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# ---------------------------------------------------------------------------
FROM base AS deps-api
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM base AS deps-connector
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra connector

FROM base AS deps-dev
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra connector

# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/src
# pip is not needed at runtime (dependencies come from the uv-built venv); removing it
# also removes its vendored packages from the attack surface.
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip* /usr/local/bin/pip* \
    && groupadd --system --gid 10001 aether \
    && useradd --system --uid 10001 --gid aether --no-create-home --shell /usr/sbin/nologin aether
WORKDIR /app
COPY backend/alembic.ini ./alembic.ini
COPY backend/src ./src
USER 10001:10001

FROM runtime AS api
COPY --from=deps-api /opt/venv /opt/venv
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "aether.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]

FROM runtime AS connector
COPY --from=deps-connector /opt/venv /opt/venv
HEALTHCHECK --interval=20s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/worker-heartbeat') < 60 else 1)"]
CMD ["python", "-m", "aether.workers.connector"]

FROM base AS dev
COPY --from=deps-dev /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/src
COPY backend/ ./
