#!/usr/bin/env bash
# Consistent online backup: pg_dump (custom format) of every application database plus
# an OpenBao Raft snapshot (encrypted by OpenBao's barrier; restoring it still needs
# the unseal keys). Ship the output directory offsite; it contains no plaintext secrets
# except what the databases hold (no cloud credentials — those live in OpenBao).
set -euo pipefail
# shellcheck source=deploy/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

OUT="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"
umask 077
mkdir -p "$OUT"
ok=0
trap '[[ $ok -eq 1 ]] || { warn "backup failed; removing partial $OUT"; rm -rf "$OUT"; }' EXIT

for db in aether keycloak temporal temporal_visibility; do
  log "dumping database $db"
  "${COMPOSE[@]}" exec -T postgres pg_dump -U postgres -d "$db" -Fc --no-owner </dev/null >"$OUT/$db.dump"
done
"${COMPOSE[@]}" exec -T postgres pg_dumpall -U postgres --roles-only --no-role-passwords </dev/null >"$OUT/roles.sql"

log "taking openbao raft snapshot"
BAO_TOKEN="$(approle_token backup)"
export BAO_TOKEN
"${COMPOSE[@]}" exec -T -e BAO_TOKEN="$BAO_TOKEN" openbao sh -c \
  'bao operator raft snapshot save /tmp/openbao.snap >/dev/null && cat /tmp/openbao.snap && rm -f /tmp/openbao.snap' \
  </dev/null >"$OUT/openbao.snap"
bao token revoke -self >/dev/null 2>&1 || true
unset BAO_TOKEN

( cd "$OUT" && sha256sum ./*.dump ./*.snap ./roles.sql >SHA256SUMS )
cat >"$OUT/MANIFEST" <<MANIFEST
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
aether_version=${AETHER_VERSION:-unknown}
host=$(hostname)
MANIFEST
ok=1
log "backup written to $OUT ($(du -sh "$OUT" | cut -f1))"
