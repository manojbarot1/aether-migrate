#!/usr/bin/env bash
# AETHER MIGRATE — Bootstrap script
# Initialises secrets, OpenBao, and runs first-time DB migrations.
# Run once after cloning on a fresh host.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_BASE="${REPO_ROOT}/deploy/compose/compose.yaml"
ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
BAO_KEYS_FILE="${REPO_ROOT}/.bao-unseal-keys"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# 1. Check prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites…"
for cmd in docker openssl; do
  if ! command -v "$cmd" &>/dev/null; then
    error "Required command not found: $cmd"
    exit 1
  fi
done

if ! docker compose version &>/dev/null; then
  error "Docker Compose v2 plugin not found. Install docker-compose-plugin."
  exit 1
fi

DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null | cut -d. -f1)
if [[ -z "$DOCKER_VERSION" ]] || [[ "$DOCKER_VERSION" -lt 25 ]]; then
  warn "Docker Engine 25+ recommended. Current: $(docker version --format '{{.Server.Version}}')"
fi

# ---------------------------------------------------------------------------
# 2. Generate .env from .env.example
# ---------------------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  info "Creating .env from .env.example…"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  info ".env created. Edit it before proceeding if needed."
fi

# ---------------------------------------------------------------------------
# 3. Generate Docker secrets using openssl
# ---------------------------------------------------------------------------
info "Generating cryptographic secrets…"

generate_secret() {
  openssl rand -base64 32 | tr -d '\n='
}

# Only generate if not already set in .env
set_env_if_missing() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "$ENV_FILE" || grep -q "^${key}=changeme" "$ENV_FILE"; then
    if grep -q "^${key}=" "$ENV_FILE"; then
      sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
      echo "${key}=${value}" >> "$ENV_FILE"
    fi
    info "  Generated ${key}"
  fi
}

set_env_if_missing "POSTGRES_PASSWORD"     "$(generate_secret)"
set_env_if_missing "KEYCLOAK_ADMIN_PASSWORD" "$(generate_secret)"
set_env_if_missing "GARAGE_RPC_SECRET"     "$(generate_secret)"

# ---------------------------------------------------------------------------
# 4. Start infrastructure services
# ---------------------------------------------------------------------------
info "Starting infrastructure services (postgres, openbao)…"
cd "$REPO_ROOT"
docker compose -f "$COMPOSE_BASE" up -d postgres openbao

# ---------------------------------------------------------------------------
# 5. Wait for PostgreSQL
# ---------------------------------------------------------------------------
info "Waiting for PostgreSQL to be healthy…"
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE_BASE" exec -T postgres \
      pg_isready -U aether -d aether &>/dev/null; then
    info "PostgreSQL is ready."
    break
  fi
  if [[ $i -eq 30 ]]; then
    error "PostgreSQL did not become ready in time."
    exit 1
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# 6. Initialize OpenBao
# ---------------------------------------------------------------------------
info "Initializing OpenBao…"
sleep 3  # give openbao time to start

BAO_INIT=$(docker compose -f "$COMPOSE_BASE" exec -T openbao \
  bao operator init -key-shares=3 -key-threshold=2 -format=json 2>/dev/null || echo "")

if [[ -z "$BAO_INIT" ]]; then
  warn "OpenBao already initialized (or running in dev mode). Skipping init."
else
  echo "$BAO_INIT" > "$BAO_KEYS_FILE"
  chmod 600 "$BAO_KEYS_FILE"
  warn "┌─────────────────────────────────────────────────────────────────────┐"
  warn "│  IMPORTANT: Unseal keys saved to .bao-unseal-keys                  │"
  warn "│  Store the unseal keys and root token OFFLINE (e.g. printed paper) │"
  warn "│  and REMOVE .bao-unseal-keys from this machine when done.          │"
  warn "└─────────────────────────────────────────────────────────────────────┘"

  # Extract root token and update .env
  BAO_ROOT_TOKEN=$(echo "$BAO_INIT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['root_token'])")
  set_env_if_missing "OPENBAO_ROOT_TOKEN" "$BAO_ROOT_TOKEN"

  # 7. Unseal OpenBao with 2 of 3 key shares
  info "Unsealing OpenBao…"
  KEYS=$(echo "$BAO_INIT" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(k) for k in d['unseal_keys_b64'][:2]]")
  while IFS= read -r key; do
    docker compose -f "$COMPOSE_BASE" exec -T openbao bao operator unseal "$key" &>/dev/null
  done <<< "$KEYS"
  info "OpenBao unsealed."
fi

# ---------------------------------------------------------------------------
# 8. Configure OpenBao secrets engines and policies
# ---------------------------------------------------------------------------
info "Configuring OpenBao secrets engines and policies…"
source "$ENV_FILE"
OPENBAO_TOKEN="${OPENBAO_ROOT_TOKEN:-${BAO_ROOT_TOKEN:-}}"

if [[ -n "$OPENBAO_TOKEN" ]]; then
  docker compose -f "$COMPOSE_BASE" exec -T -e VAULT_TOKEN="$OPENBAO_TOKEN" openbao \
    bao secrets enable -path=cloud-creds kv-v2 2>/dev/null || warn "cloud-creds already enabled"

  docker compose -f "$COMPOSE_BASE" exec -T -e VAULT_TOKEN="$OPENBAO_TOKEN" openbao \
    bao secrets enable transit 2>/dev/null || warn "transit already enabled"

  # Write policies
  for policy in connector-worker api; do
    docker compose -f "$COMPOSE_BASE" exec -T -e VAULT_TOKEN="$OPENBAO_TOKEN" openbao \
      bao policy write "$policy" "/openbao/config/policies/${policy}.hcl" 2>/dev/null || \
      warn "Policy ${policy} may not have been written (config not mounted)."
  done
  info "OpenBao configured."
else
  warn "OPENBAO_ROOT_TOKEN not set — skipping OpenBao configuration. Run manually."
fi

# ---------------------------------------------------------------------------
# 9. Run database migrations
# ---------------------------------------------------------------------------
info "Running database migrations…"
docker compose -f "$COMPOSE_BASE" run --rm migrate

# ---------------------------------------------------------------------------
# 10. Done
# ---------------------------------------------------------------------------
info ""
info "Bootstrap complete!"
info "Run 'make dev' to start all services."
info "Login at https://${DOMAIN:-localhost}"
