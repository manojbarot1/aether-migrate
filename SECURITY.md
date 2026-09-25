# Security Policy

## Reporting Vulnerabilities

**Do not open public GitHub issues for security vulnerabilities.**

To report a security vulnerability, please email the maintainers at **security@aether-migrate.example.com** (replace with your actual contact). Include:

1. A description of the vulnerability and its potential impact.
2. Steps to reproduce, including any proof-of-concept code.
3. The version(s) affected.
4. Whether you have a proposed fix.

You will receive an acknowledgement within **48 hours** and a full response within **7 days**.

We follow responsible disclosure: please give us **90 days** to patch before publishing.

---

## Supported Versions

Only the **latest release** of the `main` branch is actively supported with security patches.

---

## Security Architecture

### Credential Boundary

Cloud provider credentials (AWS access keys, Azure service principals, GCP service accounts, IBM API keys) are stored exclusively in **OpenBao** (KV v2 engine, encrypted at rest). 

- The **API service** can write (transit-encrypt) credentials but **cannot read** them.
- The **connector worker** fetches credentials at runtime using a short-lived OpenBao token with the `connector-worker` policy.
- No credentials are stored in the PostgreSQL database or passed through environment variables in production.
- Credentials are transmitted to the worker via the OpenBao `/v1/cloud-creds/{id}` path, never via the application API.

### Authentication

- All API endpoints require a valid **OIDC JWT** (issued by Keycloak) or a SHA-256-hashed **API token**.
- JWTs are validated against the Keycloak JWKS endpoint (`OIDC_JWKS_URL`). Signature algorithm is RS256.
- PKCE (S256 code challenge) is enforced for the web SPA (`aether-web` client).
- **MFA (TOTP)** is enforced for `approver` and `admin` roles via Keycloak required actions.

### Authorisation

Role hierarchy (least to most privileged):

```
viewer → analyst → connection-admin → approver → operator → admin
```

- Role claims are read from `realm_access.roles` in the Keycloak JWT.
- Enforcement is performed at both the FastAPI dependency layer and PostgreSQL Row-Level Security.

### Workspace Isolation

All database tables include a `workspace_id` column. PostgreSQL RLS policies enforce that queries only return rows matching the authenticated workspace's ID, set via `app.current_workspace_id` session variable.

### Audit Log

Every write operation records an `AuditEvent` with:
- Actor identity (`actor_id`, `actor_email`)
- Action and target
- Redacted arguments (no secrets in the log)
- SHA-256 hash chain linking each event to the previous one

The chain can be verified offline: `event_hash(n) = SHA-256(event_hash(n-1) + canonical_json(event_n))`.

### Container Security

- All containers run as **UID 1001** (non-root).
- Production containers have `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges: true`.
- Secrets are injected via Docker secrets (`/run/secrets/`), not environment variables.
- Container images are built on Red Hat UBI 9 minimal (Python services) and UBI 9 nginx-122 (web).

### TLS

All external traffic is handled by **Caddy**, which automatically provisions TLS certificates (Let's Encrypt for production, self-signed for `localhost`). Internal service-to-service communication uses Docker internal networks.

---

## Known Limitations (Phase 0)

- OpenBao in dev mode uses a non-HA single-node setup. Phase 8 adds Raft HA.
- JWT JWKS fetching uses `urllib.request` without async HTTP — Phase 1 replaces this with `httpx`.
- The Keycloak `aether-api` client secret is a placeholder in the realm export — the bootstrap script regenerates it.
