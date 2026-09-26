#!/usr/bin/env bash
# Live security self-test for a running installation. Proves, against the real
# services, the controls PROJECT_PLAN §4/§8 promise:
#
#   * the API can write cloud secrets but cannot read or list them
#   * the connector can read cloud secrets but cannot write them
#   * neither service can manage OpenBao itself
#   * only the connector has a route to the internet
#   * the database runtime role is not a superuser and cannot bypass RLS
#   * the audit hash chain verifies
#
# Exit code 0 = all controls hold. Safe to run in production (read-only apart
# from one probe secret that is written and destroyed).
set -euo pipefail

# shellcheck source=deploy/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

pass=0; fail=0
check() {  # check <description> <expected> <actual>
  if [[ "$2" == "$3" ]]; then printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1))
  else printf '  \033[31mFAIL\033[0m %s (expected %s, got %s)\n' "$1" "$2" "$3"; fail=$((fail + 1)); fi
}
pyexec() { "${COMPOSE[@]}" exec -T "$1" python -c "$2" </dev/null 2>/dev/null | tail -1; }

BAO_PROBE='
import asyncio, sys
from aether.config import get_settings
from aether.secrets.openbao import OpenBaoClient
async def main(op):
    b = OpenBaoClient.from_settings(get_settings())
    p = "ws/00000000-0000-0000-0000-000000000000/conn/selftest"
    if op == "write":
        r = await b._request("POST", f"/v1/cloud-creds/data/{p}", json={"data": {"probe": "1"}})
    elif op == "read":
        r = await b._request("GET", f"/v1/cloud-creds/data/{p}")
    elif op == "list":
        r = await b._request("LIST", "/v1/cloud-creds/metadata/ws")
    elif op == "destroy":
        r = await b._request("DELETE", f"/v1/cloud-creds/metadata/{p}")
    elif op == "sys":
        r = await b._request("GET", "/v1/sys/policies/acl")
    print(r.status_code)
asyncio.run(main(sys.argv[1]))
'
bao_op() { "${COMPOSE[@]}" exec -T "$1" python -c "$BAO_PROBE" "$2" </dev/null 2>/dev/null | tail -1; }

echo "Secret-store boundary (OpenBao)"
check "api can write a connection secret"           200 "$(bao_op api write)"
check "api cannot read connection secrets"          403 "$(bao_op api read)"
check "api cannot list connection secrets"          403 "$(bao_op api list)"
check "connector can read connection secrets"       200 "$(bao_op worker-connector read)"
check "connector cannot write connection secrets"   403 "$(bao_op worker-connector write)"
check "connector cannot list connection secrets"    403 "$(bao_op worker-connector list)"
check "api cannot administer OpenBao"               403 "$(bao_op api sys)"
check "connector cannot administer OpenBao"         403 "$(bao_op worker-connector sys)"
check "api can destroy the probe secret"            204 "$(bao_op api destroy)"

EGRESS='
import socket
try:
    socket.create_connection(("sts.amazonaws.com", 443), timeout=4); print("open")
except Exception: print("blocked")
'
echo "Network egress"
check "api has no internet route"         blocked "$(pyexec api "$EGRESS")"
check "connector reaches cloud APIs"      open    "$(pyexec worker-connector "$EGRESS")"

DB='
import asyncio
from sqlalchemy import text
from aether.config import get_settings
from aether.db.session import init_engine, session_scope
from aether.audit.writer import verify_chain
async def main():
    init_engine(get_settings())
    async with session_scope() as s:
        su, bypass = (await s.execute(text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user"))).one()
        rows = (await s.execute(text("select count(*) from cloud_connections"))).scalar()
        v = await verify_chain(s)
    print(f"{su}|{bypass}|{rows}|{v.ok}")
asyncio.run(main())
'
IFS='|' read -r su bypass rows chain <<<"$(pyexec api "$DB")"
echo "Database"
check "runtime role is not a superuser"             False "$su"
check "runtime role cannot bypass RLS"              False "$bypass"
check "no rows visible without a workspace scope"   0     "$rows"
check "audit hash chain verifies"                   True  "$chain"

echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
