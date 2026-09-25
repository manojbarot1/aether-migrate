# syntax=docker/dockerfile:1
# AETHER MIGRATE — Worker Dockerfile (DEV) — used for both domain and connector workers

FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /build
COPY pyproject.toml ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/audit/pyproject.toml packages/audit/
COPY packages/db/pyproject.toml packages/db/
COPY packages/secrets/pyproject.toml packages/secrets/
COPY packages/tools/pyproject.toml packages/tools/
COPY packages/providers/base/pyproject.toml packages/providers/base/
COPY packages/providers/aws/pyproject.toml packages/providers/aws/
COPY packages/providers/azure/pyproject.toml packages/providers/azure/
COPY packages/providers/gcp/pyproject.toml packages/providers/gcp/
COPY packages/providers/ibm/pyproject.toml packages/providers/ibm/
COPY packages/topology/pyproject.toml packages/topology/
COPY packages/sizing/pyproject.toml packages/sizing/
COPY packages/cost/pyproject.toml packages/cost/
COPY packages/assessment/pyproject.toml packages/assessment/
COPY packages/planner/pyproject.toml packages/planner/
COPY packages/iac/pyproject.toml packages/iac/
COPY packages/catalog/pyproject.toml packages/catalog/
COPY apps/worker_domain/pyproject.toml apps/worker_domain/
COPY apps/worker_connector/pyproject.toml apps/worker_connector/
COPY packages/ packages/
COPY apps/worker_domain/ apps/worker_domain/
COPY apps/worker_connector/ apps/worker_connector/
RUN uv venv /build/venv && \
    uv pip install --no-cache --python /build/venv/bin/python \
      packages/core packages/audit packages/db packages/secrets packages/tools \
      packages/providers/base packages/providers/aws packages/providers/azure \
      packages/providers/gcp packages/providers/ibm \
      packages/topology packages/sizing packages/cost packages/assessment \
      packages/planner packages/iac packages/catalog \
      apps/worker_domain apps/worker_connector

FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1001 -s /bin/false appuser
COPY --from=builder /build/venv /opt/venv
COPY --from=builder /build/packages /opt/packages
COPY --from=builder /build/apps /opt/apps
RUN find /opt/venv/lib -name "*.pth" -exec sed -i 's|/build/packages|/opt/packages|g; s|/build/apps|/opt/apps|g' {} \;
# Fix shebang paths in venv scripts (they point to /build/venv/bin/python at build time)
RUN find /opt/venv/bin -type f ! -name '*.py' -exec sed -i '1s|^#!/build/venv/bin/python|#!/usr/local/bin/python3|' {} \; 2>/dev/null || true
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 1001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "print('ok')"
# CMD is overridden per service in compose
CMD ["python", "-m", "worker_domain.main"]
