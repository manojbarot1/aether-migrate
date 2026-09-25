# syntax=docker/dockerfile:1
# AETHER MIGRATE — AI service Dockerfile
# Base: Red Hat UBI 9 minimal with Python 3.12

FROM registry.redhat.io/ubi9/ubi-minimal:latest AS builder

RUN microdnf install -y python3.12 python3.12-pip python3.12-devel gcc && \
    microdnf clean all

RUN python3.12 -m pip install --no-cache-dir uv==0.5.26

WORKDIR /build

COPY pyproject.toml ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/tools/pyproject.toml packages/tools/
COPY apps/ai/pyproject.toml apps/ai/

COPY packages/core/src packages/core/src
COPY packages/tools/src packages/tools/src
COPY apps/ai/src apps/ai/src

RUN python3.12 -m uv venv /build/venv && \
    /build/venv/bin/uv pip install \
      --no-cache \
      packages/core \
      packages/tools \
      apps/ai

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

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/livez')"

CMD ["uvicorn", "ai.main:app", "--host", "127.0.0.1", "--port", "8001", "--workers", "1"]
