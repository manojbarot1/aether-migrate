#!/usr/bin/env bash
# First-time installation of AETHER MIGRATE on a single Docker host. Idempotent:
# re-running skips anything already done.
#
#   1. generates random secrets into deploy/secrets/ (dir 0700)
#   2. starts Postgres + OpenBao, initialises and unseals OpenBao, writes policies and
#      AppRoles (api = write-only, connector = read-only, backup = snapshots), then
#      revokes the initial root token
#   3. starts the full stack
#   4. creates the first platform administrator in Keycloak
set -euo pipefail
# shellcheck source=deploy/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v docker >/dev/null || die "docker is required"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  log "created .env from .env.example; review it, then re-run"
  exit 0
fi
: "${AETHER_PUBLIC_URL:?AETHER_PUBLIC_URL must be set in .env}"
: "${AETHER_ADMIN_EMAIL:?AETHER_ADMIN_EMAIL must be set in .env}"

# ---------------------------------------------------------------- secrets
umask 077
mkdir -p "$SECRETS"
chmod 700 "$SECRETS"
for name in pg_superuser_password pg_owner_password pg_app_password pg_temporal_password \
            pg_keycloak_password keycloak_admin_password; do
  if [[ ! -s "$SECRETS/$name" ]]; then gen >"$SECRETS/$name"; log "generated secret $name"; fi
done
for svc in api connector backup; do
  for kind in role_id secret_id; do
    [[ -e "$SECRETS/bao_${svc}_$kind" ]] || : >"$SECRETS/bao_${svc}_$kind"  # placeholder until OpenBao is configured
  done
done
# Files are bind-mounted into containers running as different non-root users; the
# 0700 directory is what keeps other host users out.
chmod 644 "$SECRETS"/*
[[ -f "$SECRETS/openbao-init.json" ]] && chmod 600 "$SECRETS/openbao-init.json"

# ---------------------------------------------------------------- images
log "building images"
"${COMPOSE[@]}" build --pull -q

# ---------------------------------------------------------------- OpenBao
log "starting postgres and openbao"
"${COMPOSE[@]}" up -d postgres openbao
status="$(bao_status_json)"
[[ -n "$status" ]] || die "openbao did not start"

if [[ "$(json_field '["initialized"]' <<<"$status")" != "True" ]]; then
  log "initialising openbao (5 key shares, threshold 3)"
  bao operator init -key-shares=5 -key-threshold=3 -format=json >"$SECRETS/openbao-init.json"
  chmod 600 "$SECRETS/openbao-init.json"
  warn "unseal keys were written to deploy/secrets/openbao-init.json"
  warn "after installation, distribute them to key holders and delete the file from this host"
  unseal
  BAO_TOKEN="$(json_field '["root_token"]' <"$SECRETS/openbao-init.json")"
  export BAO_TOKEN
  configure_openbao
  bao token revoke -self >/dev/null
  unset BAO_TOKEN
  python3 - "$SECRETS/openbao-init.json" <<'PY' 2>/dev/null || warn "remove root_token from openbao-init.json manually (it is already revoked)"
import json, sys
p = sys.argv[1]
d = json.load(open(p)); d.pop("root_token", None); d.pop("root_token_revoked", None)
d["root_token_revoked"] = True
json.dump(d, open(p, "w"), indent=2)
PY
  log "initial root token revoked"
else
  unseal
  missing=0
  for svc in api connector backup; do [[ -s "$SECRETS/bao_${svc}_role_id" ]] || missing=1; done
  if [[ $missing -eq 1 ]]; then
    log "openbao AppRoles incomplete: reconfiguring with a temporary root token"
    with_root_token configure_openbao
  fi
fi

# ---------------------------------------------------------------- full stack
log "starting the full stack (first start takes a few minutes)"
"${COMPOSE[@]}" up -d --wait --wait-timeout 600

# ---------------------------------------------------------------- first admin
kcadm() { "${COMPOSE[@]}" exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null; }
kcadm config credentials --server http://localhost:8080/auth --realm master --user admin \
  --password "$(cat "$SECRETS/keycloak_admin_password")" >/dev/null
if [[ -z "$(kcadm get users -r aether -q "email=$AETHER_ADMIN_EMAIL" -q exact=true --fields id --format csv --noquotes)" ]]; then
  temp_pw="$(gen 20)"
  kcadm create users -r aether -s username="$AETHER_ADMIN_EMAIL" -s email="$AETHER_ADMIN_EMAIL" \
    -s firstName=Platform -s lastName=Administrator -s enabled=true -s emailVerified=true >/dev/null
  kcadm set-password -r aether --username "$AETHER_ADMIN_EMAIL" --new-password "$temp_pw" --temporary
  kcadm add-roles -r aether --uusername "$AETHER_ADMIN_EMAIL" --rolename platform-admin
  if [[ "${AETHER_REQUIRE_ADMIN_MFA:-true}" == "true" ]]; then
    uid="$(kcadm get users -r aether -q "email=$AETHER_ADMIN_EMAIL" -q exact=true --fields id --format csv --noquotes)"
    kcadm update "users/$uid" -r aether -s 'requiredActions=["CONFIGURE_TOTP","UPDATE_PASSWORD"]'
  fi
  log "created platform administrator $AETHER_ADMIN_EMAIL"
  printf '\n    temporary password: %s   (must be changed at first login)\n\n' "$temp_pw"
else
  log "platform administrator $AETHER_ADMIN_EMAIL already exists"
fi

log "AETHER MIGRATE is up: $AETHER_PUBLIC_URL"
log "verify the security controls with: deploy/scripts/aetherctl selftest"
