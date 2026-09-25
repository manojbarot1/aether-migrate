# AETHER MIGRATE

> Self-hosted, cloud-neutral migration control plane for AWS, Azure, GCP, and IBM Cloud.

**Phase 0 — Foundation** · All packages scaffolded, API working, Docker ready.

---

## What is AETHER MIGRATE?

AETHER MIGRATE is an open-source control plane for enterprise cloud migrations. It discovers resources across multiple cloud providers, normalises them into a unified model, assesses migration readiness, generates migration plans, and (in later phases) executes them with full audit logging and rollback support.

Unlike SaaS migration tools, AETHER MIGRATE runs entirely on your own infrastructure. Credentials never leave your network.

---

## Quick Start

### Prerequisites

- Docker Engine **25+**
- Docker Compose **v2** (`docker compose version`)
- **16 GB RAM** (for all services)
- **8 GB disk** (for images and database volumes)

### First-time setup

```bash
git clone https://github.com/your-org/aether-migrate
cd aether-migrate

# Bootstrap: generates secrets, initialises OpenBao, runs migrations
make bootstrap

# Start all services in development mode
make dev
```

Open [http://localhost](http://localhost) in your browser.

Default Keycloak admin: `admin` / (password set by bootstrap in `.env`)

---

## Architecture

```
Caddy (TLS termination)
  ├── /api/*     → FastAPI (aether-api)
  ├── /ai/*      → FastAPI (aether-ai)
  ├── /mcp/*     → FastAPI (aether-mcp)
  └── /*         → React SPA (nginx)

Temporal          → worker_domain + worker_connector
PostgreSQL 17     → all persistent data (RLS-isolated)
OpenBao           → cloud credentials, transit encryption
Keycloak 26       → OIDC, RBAC, MFA
```

See [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) for the full architecture and phase roadmap.

---

## Commands

| Command | Description |
|---------|-------------|
| `make bootstrap` | First-time setup |
| `make dev` | Start in dev mode (hot reload) |
| `make up` | Start in production mode |
| `make down` | Stop all services |
| `make logs` | Tail all logs |
| `make test` | Run Python test suite |
| `make lint` | Run ruff + ESLint |
| `make typecheck` | Run mypy |
| `make build` | Build all Docker images |
| `make backup` | Back up PostgreSQL and OpenBao |
| `make upgrade` | Pull, rebuild, migrate, restart |

---

## Project Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Foundation (this PR) | ✅ Complete |
| 1 | UI Shell | ⬜ |
| 2a | AWS Discovery | ⬜ |
| 2b | Azure Discovery | ⬜ |
| 2c | GCP Discovery | ⬜ |
| 2d | IBM Cloud Discovery | ⬜ |
| 3 | AI Assistant | ⬜ |
| 4 | Migration Planner | ⬜ |
| 5 | Execute Mode | ⬜ |
| 6 | IaC Generation | ⬜ |
| 7 | MCP Server | ⬜ |
| 8 | HA & Hardening | ⬜ |

---

## Security

See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy and security architecture.

**TL;DR**: Cloud credentials are stored in OpenBao. The API cannot read them. The connector worker fetches them at runtime. All actions are logged in a tamper-evident SHA-256 hash chain. MFA is required for approver and admin roles.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Licence

Apache 2.0
