# syntax=docker/dockerfile:1
# AETHER MIGRATE — Temporal worker Dockerfile (shared image for domain + connector workers)
# The CMD is overridden in compose to run the correct worker module.

FROM registry.redhat.io/ubi9/ubi-minimal:latest AS builder

RUN microdnf install -y python3.12 python3.12-pip python3.12-devel gcc && \
    microdnf clean all

RUN python3.12 -m pip install --no-cache-dir uv==0.5.26

WORKDIR /build

COPY pyproject.toml ./
# Core packages
COPY packages/core/pyproject.toml packages/core/
COPY packages/audit/pyproject.toml packages/audit/
COPY packages/db/pyproject.toml packages/db/
COPY packages/tools/pyproject.toml packages/tools/
COPY packages/providers/base/pyproject.toml packages/providers/base/
COPY packages/providers/aws/pyproject.toml packages/providers/aws/
COPY packages/providers/azure/pyproject.toml packages/providers/azure/
COPY packages/providers/gcp/pyproject.toml packages/providers/gcp/
COPY packages/providers/ibm/pyproject.toml packages/providers/ibm/
COPY packages/catalog/pyproject.toml packages/catalog/
COPY packages/sizing/pyproject.toml packages/sizing/
COPY packages/cost/pyproject.toml packages/cost/
COPY packages/assessment/pyproject.toml packages/assessment/
COPY packages/planner/pyproject.toml packages/planner/
COPY apps/worker_domain/pyproject.toml apps/worker_domain/
COPY apps/worker_connector/pyproject.toml apps/worker_connector/

# Source trees
COPY packages/ packages/
COPY apps/worker_domain/src apps/worker_domain/src
COPY apps/worker_connector/src apps/worker_connector/src

RUN python3.12 -m uv venv /build/venv && \
    /build/venv/bin/uv pip install \
      --no-cache \
      packages/core \
      packages/audit \
      packages/db \
      packages/tools \
      packages/providers/base \
      packages/providers/aws \
      packages/providers/azure \
      packages/providers/gcp \
      packages/providers/ibm \
      packages/catalog \
      packages/sizing \
      packages/cost \
      packages/assessment \
      packages/planner \
      apps/worker_domain \
      apps/worker_connector

FROM registry.redhat.io/ubi9/ubi-minimal:latest AS runtime

RUN microdnf install -y python3.12 && \
    microdnf clean all

RUN useradd -m -u 1001 -s /sbin/nologin appuser

COPY --from=builder /build/venv /opt/venv

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 1001

# Override in compose with the appropriate worker module
CMD ["python3.12", "-m", "worker_domain.main"]
