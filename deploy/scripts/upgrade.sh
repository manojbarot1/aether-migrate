#!/usr/bin/env bash
# AETHER MIGRATE — Upgrade script
# Pulls latest images, runs migrations, and performs a rolling restart.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_BASE="${REPO_ROOT}/deploy/compose/compose.yaml"

echo "[INFO] Starting upgrade…"
echo "[INFO] Repo root: ${REPO_ROOT}"

# Pull latest base images
echo "[INFO] Pulling latest images…"
docker compose -f "$COMPOSE_BASE" pull postgres temporal keycloak openbao

# Rebuild application images
echo "[INFO] Rebuilding application images…"
docker compose -f "$COMPOSE_BASE" build api ai mcp worker_domain worker_connector web

# Run migrations (non-destructive)
echo "[INFO] Running database migrations…"
docker compose -f "$COMPOSE_BASE" run --rm migrate

# Rolling restart (one service at a time to minimise downtime)
echo "[INFO] Restarting services…"
for svc in worker_connector worker_domain api ai mcp web edge; do
  echo "[INFO]   Restarting ${svc}…"
  docker compose -f "$COMPOSE_BASE" restart "$svc"
  sleep 3
done

echo "[INFO] Upgrade complete."
echo "[INFO] Run 'make logs' to monitor service health."
