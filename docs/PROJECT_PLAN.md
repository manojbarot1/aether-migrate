# AETHER MIGRATE — Project Plan

**Phase 0 Foundation** · Self-hosted cloud-neutral migration control plane

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  Browser                                                │
│  React 19 + Vite SPA (TanStack Router, TanStack Query)  │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS (Caddy)
     ┌─────────────────┼──────────────────────┐
     │                 │                      │
  /api/*            /ai/*                 /mcp/*
     │                 │                      │
  FastAPI           FastAPI              FastAPI
  (aether-api)     (aether-ai)         (aether-mcp)
     │                 │                      │
     └────────┬─────── ┘                      │
              │                               │
       PostgreSQL 17          ┌───────────────┘
       (RLS isolation)        │ MCP Protocol
              │               │
       Temporal Server        │
       (workflow engine)      ▼
              │         External AI Clients
    ┌─────────┴──────────┐
    │                    │
 worker_domain     worker_connector
 (sizing, cost,    (discovery, provider
  assessment,       adapters)
  planner)               │
                   OpenBao (secrets)
                   Cloud Providers (AWS/Azure/GCP/IBM)
```

---

## Project Phases

| Phase | Name | Key Deliverables | Status |
|-------|------|-----------------|--------|
| **0** | Foundation | Workspace structure, all packages stubbed, API, DB, Docker, CI | ✅ Complete |
| **1** | UI Shell | Full React SPA with all routes, data tables, topology graph | ⬜ Next |
| **2a** | AWS Discovery | EC2, EBS, VPC, SG discovery; CloudWatch metrics; EC2 pricing catalog | ⬜ |
| **2b** | Azure Discovery | VM, Managed Disk, VNet, NSG discovery; Monitor metrics; Retail Prices | ⬜ |
| **2c** | GCP Discovery | Compute Engine discovery; Cloud Monitoring; Billing Catalog | ⬜ |
| **2d** | IBM Cloud Discovery | VPC VSI discovery; IBM Monitoring; Global Catalog pricing | ⬜ |
| **3** | AI Assistant | pydantic-ai agent, tool registry, streaming chat, sizing/cost tools | ⬜ |
| **4** | Planner | Wave scheduling, dependency ordering, rollback plans, approval flow | ⬜ |
| **5** | Execute Mode | Temporal execution workflows, dry-run, change audit | ⬜ |
| **6** | IaC Generation | OpenTofu module generation per migration plan | ⬜ |
| **7** | MCP Server | Full MCP 1.0 protocol, tool manifest, external AI client auth | ⬜ |
| **8** | HA & Hardening | Multi-replica, auto-unseal, backup automation, pen test | ⬜ |

---

## Package Dependency Graph

```
aether-core
├── aether-audit          (hash-chain audit log)
├── aether-db             (SQLAlchemy ORM + Alembic)
├── aether-tools          (tool registry)
├── aether-providers-base (adapter protocol)
│   ├── aether-providers-aws
│   ├── aether-providers-azure
│   ├── aether-providers-gcp
│   └── aether-providers-ibm
├── aether-catalog        (instance type / pricing)
│   ├── aether-sizing
│   └── aether-cost
│       └── aether-assessment
│           └── aether-planner
│               └── aether-iac
```

---

## Security Architecture

- **Credential boundary**: Cloud secrets never leave OpenBao. The connector worker fetches them at execution time using a short-lived OpenBao token. The API service can only transit-encrypt (write) credentials, not decrypt them.
- **Authentication**: All API endpoints require a valid Keycloak OIDC JWT or a SHA-256-hashed API token. MFA (TOTP) is enforced for `approver` and `admin` roles.
- **Authorisation**: RBAC via JWT `realm_access.roles` claim. Role hierarchy: viewer < analyst < connection-admin < approver < operator < admin.
- **Audit log**: Tamper-evident SHA-256 hash chain. Every write operation records an `AuditEvent`. The chain can be verified offline.
- **Workspace isolation**: PostgreSQL Row-Level Security policies on all tables ensure cross-workspace data leakage is impossible at the DB layer.
- **Container security**: All containers run as UID 1001 (non-root), `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges`. Secrets injected via Docker secrets (`/run/secrets/`), never environment variables in production.

---

## Technology Decisions

See `docs/adr/` for all Architecture Decision Records:

- [ADR-001](adr/001-use-temporal.md) — Temporal for workflows
- [ADR-002](adr/002-use-openbao.md) — OpenBao for secrets
- [ADR-003](adr/003-use-keycloak.md) — Keycloak for identity
- [ADR-004](adr/004-postgresql-only.md) — PostgreSQL only
- [ADR-005](adr/005-react-vite-spa.md) — React + Vite SPA
- [ADR-006](adr/006-pydantic-ai.md) — pydantic-ai for model orchestration
- [ADR-007](adr/007-n-plus-m-adapters.md) — N+M adapter pattern
- [ADR-008](adr/008-opentofu-iac.md) — OpenTofu for IaC generation
