# syntax=docker/dockerfile:1
# AETHER MIGRATE — MCP service Dockerfile (DEV)

FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /build
COPY pyproject.toml ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/audit/pyproject.toml packages/audit/
COPY packages/db/pyproject.toml packages/db/
COPY packages/secrets/pyproject.toml packages/secrets/
COPY packages/tools/pyproject.toml packages/tools/
COPY packages/providers/base/pyproject.toml packages/providers/base/
COPY packages/topology/pyproject.toml packages/topology/
COPY packages/sizing/pyproject.toml packages/sizing/
COPY packages/cost/pyproject.toml packages/cost/
COPY packages/assessment/pyproject.toml packages/assessment/
COPY packages/planner/pyproject.toml packages/planner/
COPY packages/iac/pyproject.toml packages/iac/
COPY apps/mcp/pyproject.toml apps/mcp/
COPY packages/ packages/
COPY apps/mcp/ apps/mcp/
RUN uv venv /build/venv && \
    uv pip install --no-cache --python /build/venv/bin/python \
      packages/core packages/audit packages/db packages/secrets packages/tools \
      packages/providers/base packages/topology packages/sizing packages/cost \
      packages/assessment packages/planner packages/iac apps/mcp

FROM python:3.12-slim AS runtime
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
EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/livez')"
CMD ["python", "-m", "uvicorn", "mcp_server.main:app", "--host", "127.0.0.1", "--port", "8002"]
