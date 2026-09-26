#!/usr/bin/env bash
# Restore a backup made by backup.sh into this installation. DESTRUCTIVE: replaces the
# current databases and OpenBao data. Requires the OpenBao unseal keys of the backup.
set -euo pipefail
# shellcheck source=deploy/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

IN="${1:?usage: restore.sh <backup-dir>}"
[[ -f "$IN/SHA256SUMS" ]] || die "$IN does not look like a backup directory"
( cd "$IN" && sha256sum -c --quiet SHA256SUMS ) || die "backup checksum verification failed"

if [[ "${AETHER_RESTORE_CONFIRM:-}" != "yes" ]]; then
  read -rp "This REPLACES all AETHER data with the backup from $IN. Type 'restore' to continue: " ans </dev/tty
  [[ "$ans" == "restore" ]] || die "aborted"
fi

log "stopping application services"
"${COMPOSE[@]}" stop edge api worker-connector keycloak temporal >/dev/null
"${COMPOSE[@]}" up -d postgres openbao >/dev/null

for db in aether keycloak temporal temporal_visibility; do
  case "$db" in
    aether) owner=aether_owner ;;
    keycloak) owner=keycloak ;;
    *) owner=temporal ;;
  esac
  log "restoring database $db"
  "${COMPOSE[@]}" exec -T postgres pg_restore -U postgres -d "$db" --clean --if-exists --no-owner \
    --role="$owner" <"$IN/$db.dump"
done

log "restoring openbao snapshot (you will be asked for unseal keys if they are not on this host)"
unseal
# `docker cp` cannot write into a read-only container; stream into its tmpfs instead.
"${COMPOSE[@]}" exec -T openbao sh -c 'cat > /tmp/openbao.snap' <"$IN/openbao.snap"
with_root_token bao operator raft snapshot restore -force /tmp/openbao.snap
"${COMPOSE[@]}" exec -T openbao rm -f /tmp/openbao.snap </dev/null
unseal

log "starting the stack"
"${COMPOSE[@]}" up -d --wait --wait-timeout 600
log "restore complete; run 'aetherctl selftest' to verify"
