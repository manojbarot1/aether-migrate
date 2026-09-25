# syntax=docker/dockerfile:1
# AETHER MIGRATE — MCP server Dockerfile
# Base: Red Hat UBI 9 minimal with Python 3.12

FROM registry.redhat.io/ubi9/ubi-minimal:latest AS builder

RUN microdnf install -y python3.12 python3.12-pip python3.12-devel gcc && \
    microdnf clean all

RUN python3.12 -m pip install --no-cache-dir uv==0.5.26

WORKDIR /build

COPY pyproject.toml ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/tools/pyproject.toml packages/tools/
COPY apps/mcp/pyproject.toml apps/mcp/

COPY packages/core/src packages/core/src
COPY packages/tools/src packages/tools/src
COPY apps/mcp/src apps/mcp/src

RUN python3.12 -m uv venv /build/venv && \
    /build/venv/bin/uv pip install \
      --no-cache \
      packages/core \
      packages/tools \
      apps/mcp

FROM registry.redhat.io/ubi9/ubi-minimal:latest AS runtime

RUN microdnf install -y python3.12 curl && \
    microdnf clean all

RUN useradd -m -u 1001 -s /sbin/nologin appuser

COPY --from=builder /build/venv /opt/venv

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 1001

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/livez')"

CMD ["uvicorn", "mcp_server.main:app", "--host", "127.0.0.1", "--port", "8002", "--workers", "1"]
