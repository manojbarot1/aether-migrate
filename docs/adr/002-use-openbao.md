# ADR-002: Use OpenBao for Secrets Management

**Status:** Accepted  
**Date:** 2025-01-01

## Context

AETHER MIGRATE stores cloud provider credentials (AWS access keys, Azure service principals, GCP service account keys, IBM API keys). These must be stored encrypted at rest, accessed only by the connector worker at discovery time, and auditable.

HashiCorp Vault was the original choice, but its BSL licence (2.0+) conflicts with the self-hosted, open-source distribution goal.

## Decision

Use **OpenBao** — the community fork of Vault maintained under the MPL-2.0 licence. OpenBao is API-compatible with Vault, so the tooling and client libraries are identical. The KV v2 engine stores credentials; the Transit engine provides envelope encryption for any secrets the API needs to write without being able to read them.

## Consequences

- **+** Open-source licence compatible with self-hosted distribution.
- **+** API/client library compatibility with Vault ecosystem.
- **+** Transit encryption enforces the "API cannot read secrets" constraint.
- **−** Smaller community than HashiCorp Vault; some enterprise features absent.
- **−** Requires careful unseal-key management in production (see bootstrap script).
