#!/usr/bin/env bash
# AETHER MIGRATE — Backup script
# Creates a timestamped backup of PostgreSQL data and OpenBao data.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_BASE="${REPO_ROOT}/deploy/compose/compose.yaml"
BACKUP_DIR="${REPO_ROOT}/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

source "${REPO_ROOT}/.env" 2>/dev/null || true

mkdir -p "$BACKUP_DIR"

echo "[INFO] Starting backup ${TIMESTAMP}…"

# PostgreSQL dump
echo "[INFO] Dumping PostgreSQL…"
docker compose -f "$COMPOSE_BASE" exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-aether}" -d "${POSTGRES_DB:-aether}" --no-password \
  | gzip > "${BACKUP_DIR}/postgres_${TIMESTAMP}.sql.gz"

echo "[INFO] PostgreSQL backup saved to: ${BACKUP_DIR}/postgres_${TIMESTAMP}.sql.gz"

# OpenBao snapshot (requires unsealed BAO)
if [[ -n "${OPENBAO_ROOT_TOKEN:-}" ]]; then
  echo "[INFO] Snapshotting OpenBao…"
  docker compose -f "$COMPOSE_BASE" exec -T \
    -e VAULT_TOKEN="$OPENBAO_ROOT_TOKEN" \
    openbao bao operator raft snapshot save /tmp/openbao_snapshot_${TIMESTAMP}.snap 2>/dev/null || true
  docker compose -f "$COMPOSE_BASE" cp \
    openbao:/tmp/openbao_snapshot_${TIMESTAMP}.snap \
    "${BACKUP_DIR}/openbao_${TIMESTAMP}.snap" 2>/dev/null || \
    echo "[WARN] OpenBao snapshot failed — may not be in raft mode."
fi

echo "[INFO] Backup complete: ${BACKUP_DIR}/"
echo "[INFO] Files: $(ls -1 ${BACKUP_DIR}/*${TIMESTAMP}* 2>/dev/null | wc -l) file(s)"
