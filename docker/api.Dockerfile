# syntax=docker/dockerfile:1
# AETHER MIGRATE — API service Dockerfile
# Base: Red Hat UBI 9 minimal with Python 3.12 (installed via microdnf)
# Security: non-root user (UID 1001), no hardcoded secrets

# ---------------------------------------------------------------------------
# Stage 1: builder — install Python deps with uv
# ---------------------------------------------------------------------------
FROM registry.redhat.io/ubi9/ubi-minimal:latest AS builder

RUN microdnf install -y python3.12 python3.12-pip python3.12-devel gcc && \
    microdnf clean all

# Install uv for fast dependency resolution
RUN python3.12 -m pip install --no-cache-dir uv==0.5.26

WORKDIR /build

# Copy workspace config and package manifests first (layer cache)
COPY pyproject.toml ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/audit/pyproject.toml packages/audit/
COPY packages/db/pyproject.toml packages/db/
COPY apps/api/pyproject.toml apps/api/

# Copy source trees
COPY packages/core/src packages/core/src
COPY packages/audit/src packages/audit/src
COPY packages/db/src packages/db/src
COPY apps/api/src apps/api/src

# Install into /build/venv
RUN python3.12 -m uv venv /build/venv && \
    /build/venv/bin/uv pip install \
      --no-cache \
      packages/core \
      packages/audit \
      packages/db \
      apps/api

# ---------------------------------------------------------------------------
# Stage 2: runtime — minimal image, non-root user
# ---------------------------------------------------------------------------
FROM registry.redhat.io/ubi9/ubi-minimal:latest AS runtime

RUN microdnf install -y python3.12 curl && \
    microdnf clean all

# Create non-root user
RUN useradd -m -u 1001 -s /sbin/nologin appuser

COPY --from=builder /build/venv /opt/venv

WORKDIR /app

# Secrets are read from /run/secrets/ — never from environment variables
# Example: DATABASE_URL reads postgres password from /run/secrets/postgres_password
# The entrypoint script should compose the URL at runtime.

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/livez')"

CMD ["uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "2"]
