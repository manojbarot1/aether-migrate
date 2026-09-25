#!/usr/bin/env bash
# AETHER MIGRATE — Secret rotation script
# Rotates PostgreSQL password and updates .env and OpenBao.
# WARNING: All services will be restarted after rotation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_BASE="${REPO_ROOT}/deploy/compose/compose.yaml"
ENV_FILE="${REPO_ROOT}/.env"

source "$ENV_FILE"

echo "[INFO] Starting secret rotation…"
echo "[WARN] All services will be restarted. Press Ctrl+C to cancel."
sleep 5

NEW_POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '\n=')

# Update PostgreSQL password
echo "[INFO] Rotating PostgreSQL password…"
docker compose -f "$COMPOSE_BASE" exec -T postgres \
  psql -U "${POSTGRES_USER:-aether}" -c \
  "ALTER USER ${POSTGRES_USER:-aether} WITH PASSWORD '${NEW_POSTGRES_PASSWORD}';"

# Update .env
sed -i.bak "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${NEW_POSTGRES_PASSWORD}|" "$ENV_FILE"
echo "[INFO] Updated POSTGRES_PASSWORD in .env"

# Restart services that use the DB password
echo "[INFO] Restarting services…"
docker compose -f "$COMPOSE_BASE" restart api worker_domain worker_connector

echo "[INFO] Secret rotation complete."
echo "[WARN] Remove the .env.bak backup file once you verify services are healthy."
