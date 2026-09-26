# Review of the v0.1.0 implementation (commit `4f19950`)

**Date:** 2026-09-26 · **Outcome:** foundation rebuilt; domain code to be re-introduced phase by phase with integration tests.

The v0.1.0 tree (~41k lines, 275 files, produced in one autonomous session) followed the plan's directory layout and passed its own 332 tests. Those tests mock the database, OpenBao, Temporal and the identity provider, so none of the defects below could surface. Each finding was verified against the code or a running system.

## Blocking defects

| # | Area | Finding | Evidence |
|---|---|---|---|
| 1 | Tenant isolation | RLS policies read `current_setting('app.current_workspace_id')`, but no code ever sets it. All services connect as `aether`, which is the Postgres **superuser** (`POSTGRES_USER`), so RLS is bypassed entirely. If a non-superuser role were used, every query would error. | `grep -rn set_config` → no matches; `deploy/compose/compose.yaml` `DATABASE_URL=…aether:…` with `POSTGRES_USER=aether` |
| 2 | Credential boundary | Every service (api, ai, mcp, workers) receives `OPENBAO_TOKEN=devroot`, the **root token**, and OpenBao runs in dev mode (in-memory, auto-unsealed). The API can read every cloud credential. | `deploy/compose/compose.dev.yaml` |
| 3 | Audit log | The INSERT uses `:arguments_redacted::jsonb`. SQLAlchemy parses the bind as `arguments_redacte` and leaves `:arguments_redacted::jsonb` in the SQL, so **every audited write fails** against a real database. | `text(":arguments_redacted::jsonb")._bindparams` → `['arguments_redacte', …]` |
| 4 | Audit log | The chain is serialised with a per-instance `asyncio.Lock`, but a new writer is created per request, so concurrent requests fork the chain. Ordering is by `created_at` (ties possible). The writer also commits the caller's session mid-request. No UPDATE/DELETE protection exists in the database. | `packages/audit/src/audit/writer.py` |
| 5 | Authentication | JWKS is downloaded **synchronously on every request** with `urllib` inside the event loop (blocking, and a DoS amplifier against Keycloak). It uses `python-jose` (unmaintained). | `apps/api/src/api/auth.py` |
| 6 | Authorisation | Roles are global Keycloak realm roles, and the workspace comes from a custom `workspace_id` JWT claim. A user can belong to only one workspace, and roles can't differ per workspace (plan §8.1). A realm-level `admin` can target any workspace via a query parameter. | `apps/api/src/api/auth.py`, `dependencies.py` |
| 7 | Supply chain | `latest` tags for Temporal, Keycloak, SeaweedFS and Caddy. No lockfile for the web app (`npm install --legacy-peer-deps`). Runtime images are made to work by `sed`-patching `.pth` files and shebangs of a venv built at a different path. | `deploy/compose/compose.yaml`, `docker/*.Dockerfile`, `CHAT.md` §11 |
| 8 | Edge | The dev Caddyfile serves plain HTTP. The API exposes `/api/docs` and `/api/openapi.json` publicly, with permissive CORS. | `deploy/config/caddy/Caddyfile.dev`, `apps/api/src/api/main.py` |

## Structural concerns

- **20 separately packaged workspace members** for one team and one deployable. Every package is installed editable into every image, so the credential boundary is not reflected in the images: the API image contains `boto3` and the provider adapters.
- **Domain engines mixed with persistence.** For example, the sizing engine queries SQLAlchemy directly, and its `catalog_version = all_rows[0]` mixes catalog versions. Pure functions over explicit inputs would be testable and deterministic.
- **Tests exercise mocks, not behaviour.** There is no test against Postgres, OpenBao or Temporal. "All containers Up" was the acceptance criterion.

## What was kept

- The overall plan, the phase structure and the product scope.
- Ideas and data in the assessment rules (OS end-of-life tables, rule catalogue) and the AWS normalizers. These will be ported, with tests against recorded fixtures and a real database, when Phases 2a and 6 land.

## What replaced it (this PR)

A single `aether` Python package with enforced import boundaries. Every control below is verified against the real services:

- **Credential boundary:** API AppRole = write-only, connector AppRole = read-only, root token revoked after bootstrap. Break-glass needs the unseal-key quorum.
- **Database:** the runtime role is not a superuser, RLS is set per transaction, and audit rows are append-only (grants plus a trigger) and hash-chained under an advisory lock.
- **Authentication:** OIDC with cached JWKS, issuer/audience/expiry checks, and per-workspace roles held in the application database.
- **Network:** only the connector has internet egress; every other network is internal.
- **Images:** hardened (read-only rootfs, cap_drop ALL, no-new-privileges), pinned, and 0 fixable HIGH/CRITICAL findings in Trivy.
- **Operations:** `aetherctl selftest` proves 15 controls on a live installation. A backup/restore drill has been executed.
