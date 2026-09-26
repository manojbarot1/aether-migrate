# ADR 0003 — Credential boundary: write-only API, read-only connector

**Status:** accepted

**Decision.** Cloud credentials are stored only in OpenBao KV v2 (`cloud-creds/ws/<workspace>/conn/<connection>`). There are two AppRoles:

- `aether-api`: `create`, `update` and metadata `delete`. **No `read`, no `list`.**
- `aether-connector`: `read` only.

The initial root token is revoked. Administration uses a break-glass root token minted from a quorum of unseal keys. The connector worker is the only service on a network with internet egress.

**Why.** A compromised API or UI (the largest attack surface) cannot exfiltrate cloud credentials. A compromised connector can read them but cannot plant new ones, and it has no inbound exposure.

**Verification.** `aetherctl selftest` probes each permission against the live OpenBao.
