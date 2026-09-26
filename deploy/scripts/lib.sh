# Shared helpers for AETHER MIGRATE operator scripts. Source, don't execute.
# shellcheck shell=bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS="$ROOT/deploy/secrets"
if [[ -f "$ROOT/.env" ]]; then set -a; source "$ROOT/.env"; set +a; fi
COMPOSE=(docker compose --project-directory "$ROOT/deploy/compose" -f "$ROOT/deploy/compose/compose.yaml")
[[ -f "$ROOT/.env" ]] && COMPOSE+=(--env-file "$ROOT/.env")
# Space-separated list of overlay files, relative to the repository root.
for overlay in ${AETHER_COMPOSE_OVERLAY:-}; do COMPOSE+=(-f "$ROOT/$overlay"); done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# N (default 40) random alphanumerics. No pipe into `head`, which SIGPIPEs under pipefail.
gen() {
  local s
  s="$(head -c 1024 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')"
  printf '%s' "${s:0:${1:-40}}"
}

# json_field '["key"]' < file  — tiny JSON accessor (host python3, else a container).
json_field() {
  local expr="$1" input
  input="$(cat)"
  python3 -c "import json,sys; print(json.loads(sys.stdin.read())$expr)" <<<"$input" 2>/dev/null \
    || docker run --rm -i python:3.13-slim-trixie python -c "import json,sys; print(json.loads(sys.stdin.read())$expr)" <<<"$input"
}

# bao <args…>  — run the OpenBao CLI inside the container (uses $BAO_TOKEN if set).
bao() { "${COMPOSE[@]}" exec -T -e BAO_TOKEN="${BAO_TOKEN:-}" openbao bao "$@" </dev/null; }
bao_stdin() { "${COMPOSE[@]}" exec -T -e BAO_TOKEN="${BAO_TOKEN:-}" openbao bao "$@"; }

bao_status_json() {
  local s=""
  for _ in $(seq 1 60); do
    s="$(bao status -format=json 2>/dev/null || true)"
    [[ -n "$s" ]] && break
    sleep 1
  done
  printf '%s' "$s"
}

# Unseal keys: from deploy/secrets/openbao-init.json if still on this host, else prompted.
unseal_keys() {
  if [[ -f "$SECRETS/openbao-init.json" ]]; then
    local code='import json,sys; print("\n".join(json.load(sys.stdin)["unseal_keys_b64"][:3]))'
    python3 -c "$code" <"$SECRETS/openbao-init.json" 2>/dev/null \
      || docker run --rm -i python:3.13-slim-trixie python -c "$code" <"$SECRETS/openbao-init.json"
  else
    local k
    for i in 1 2 3; do read -rsp "unseal key $i: " k </dev/tty; echo >&2; printf '%s\n' "$k"; done
  fi
}

unseal() {
  "${COMPOSE[@]}" up -d openbao >/dev/null 2>&1
  local status
  status="$(bao_status_json)"
  [[ -n "$status" ]] || die "openbao is not responding"
  if grep -q '"sealed": false' <<<"$status"; then log "openbao already unsealed"; return; fi
  while read -r key; do
    bao operator unseal "$key" >/dev/null
  done < <(unseal_keys)
  bao status >/dev/null || die "openbao is still sealed"
  log "openbao unsealed"
}

# with_root_token <cmd…> — break-glass: mint a root token from a quorum of unseal keys,
# run <cmd…> with BAO_TOKEN set, then revoke it. The quorum exchange runs in a one-off
# container on the internal `secrets` network (bao_root.py); keys are passed on stdin.
# Every step is recorded in OpenBao's audit log.
with_root_token() {
  BAO_TOKEN="$(unseal_keys | "${COMPOSE[@]}" run --rm --no-deps -T \
    -v "$ROOT/deploy/scripts/bao_root.py:/opt/bao_root.py:ro" --entrypoint python api /opt/bao_root.py)" \
    || die "could not generate a break-glass root token"
  export BAO_TOKEN
  local rc=0
  "$@" || rc=$?
  # After a snapshot restore the token no longer exists (it was minted after the
  # snapshot), so a failed revoke there is expected.
  bao token revoke -self >/dev/null 2>&1 || log "temporary root token already invalid"
  unset BAO_TOKEN
  return $rc
}

# Idempotent OpenBao configuration. Needs BAO_TOKEN with root privileges.
configure_openbao() {
  log "configuring openbao: kv-v2, approle, policies"
  bao secrets list -format=json | grep -q '"cloud-creds/"' || bao secrets enable -path=cloud-creds -version=2 kv
  bao auth list -format=json | grep -q '"approle/"' || bao auth enable approle
  local svc
  for svc in api connector backup; do
    bao_stdin policy write "aether-$svc" - <"$ROOT/deploy/config/openbao/policy-$svc.hcl" >/dev/null
    bao write "auth/approle/role/aether-$svc" token_policies="aether-$svc" \
      token_ttl=1h token_max_ttl=4h secret_id_ttl=0 token_type=service >/dev/null
    if [[ ! -s "$SECRETS/bao_${svc}_role_id" || ! -s "$SECRETS/bao_${svc}_secret_id" ]]; then
      bao read -field=role_id "auth/approle/role/aether-$svc/role-id" >"$SECRETS/bao_${svc}_role_id"
      bao write -f -field=secret_id "auth/approle/role/aether-$svc/secret-id" >"$SECRETS/bao_${svc}_secret_id"
      log "issued AppRole credentials for aether-$svc"
    fi
  done
  chmod 644 "$SECRETS"/bao_*
}

# approle_token <svc> — log in with a service AppRole (used by backup).
approle_token() {
  bao write -field=token auth/approle/login \
    role_id="$(cat "$SECRETS/bao_$1_role_id")" secret_id="$(cat "$SECRETS/bao_$1_secret_id")" | tr -d '\r\n'
}
