#!/usr/bin/env bash
# AETHER MIGRATE — Restore script
# Restores a PostgreSQL backup created by backup.sh.
# Usage: ./restore.sh <backup_file.sql.gz>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_BASE="${REPO_ROOT}/deploy/compose/compose.yaml"

source "${REPO_ROOT}/.env" 2>/dev/null || true

BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" ]] || [[ ! -f "$BACKUP_FILE" ]]; then
  echo "[ERROR] Usage: $0 <backup_file.sql.gz>"
  echo "[ERROR] Available backups:"
  ls "${REPO_ROOT}/backups/"*.sql.gz 2>/dev/null || echo "  (none found)"
  exit 1
fi

echo "[WARN] This will DROP and recreate the '${POSTGRES_DB:-aether}' database."
read -r -p "Type 'yes' to continue: " confirm
if [[ "$confirm" != "yes" ]]; then
  echo "[INFO] Restore cancelled."
  exit 0
fi

echo "[INFO] Restoring from: ${BACKUP_FILE}"

# Drop and recreate DB
docker compose -f "$COMPOSE_BASE" exec -T postgres \
  psql -U "${POSTGRES_USER:-aether}" -c "DROP DATABASE IF EXISTS ${POSTGRES_DB:-aether};" postgres

docker compose -f "$COMPOSE_BASE" exec -T postgres \
  psql -U "${POSTGRES_USER:-aether}" -c "CREATE DATABASE ${POSTGRES_DB:-aether};" postgres

# Restore
gunzip -c "$BACKUP_FILE" | docker compose -f "$COMPOSE_BASE" exec -T postgres \
  psql -U "${POSTGRES_USER:-aether}" -d "${POSTGRES_DB:-aether}"

echo "[INFO] Restore complete."
echo "[INFO] Run 'make up' to restart services."
