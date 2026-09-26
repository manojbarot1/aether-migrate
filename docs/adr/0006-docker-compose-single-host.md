# ADR 0006 — Docker Compose on a single host for v1

**Status:** accepted

**Decision.** Production v1 is one Docker host running Compose v2. It includes hardened services, internal networks, Docker secrets for bootstrap credentials, backup/restore scripts and a live self-test.

**Consequences.** It is not highly available (documented RTO ≤ 2 h via restore). Every service is stateless or has a clear data volume, is configured through environment variables and files, and exposes health endpoints. A Helm chart can follow without code changes.
