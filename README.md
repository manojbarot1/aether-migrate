# AETHER MIGRATE

A self-hosted, cloud-neutral migration control plane. It lets engineers discover, understand, cost and plan workload moves across **AWS, Azure, Google Cloud and IBM Cloud**. Every number is traceable to data, and every action is traceable to a person.

> **Status: v1.0-rc (Phases 0–2a, 4–7): the read-only AWS → Azure path is complete.**
>
> - **Works today:** connect AWS accounts read-only, with least-privilege, audited, isolated credentials. Discover VMs, disks, networks, security groups and load balancers across regions, with coverage reporting. Search the normalized inventory and inspect any resource with its relationships. Visualise network topology. Size and price VMs on Azure from live list prices (1y/3y reservations, disks, one-time migration costs, any currency). Assess migration readiness with explainable rules. Generate versioned, hash-signed migration plans with waves, rollback and a validated OpenTofu landing zone, approved under four-eyes review.
> - **Next:** the AI assistant (Phase 3), more source/target providers (2b/5b), then dry-run and execution (8–10) follow the phases in [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) §24.
>
> **No cloud resource is ever modified by this release.**

## Why it is built this way

| Principle | How it is enforced |
|---|---|
| The platform must not become the weakest link | Cloud credentials live in **OpenBao**. The API's AppRole can *write* them but never *read* them. Only the isolated connector worker can read them, and it is the only container with internet egress. |
| Tenants never see each other's data | Per-workspace roles, plus PostgreSQL **row-level security** set per transaction. The runtime DB role is not a superuser and cannot bypass RLS. |
| Everything is attributable | Append-only, **hash-chained audit log**: DB grants and a trigger block UPDATE/DELETE/TRUNCATE, even for the owner, and tampering is detected by `verify`. Every cloud API call is recorded. |
| Long-running work survives failures | **Temporal** workflows. Workflow payloads carry IDs only, never secrets. |
| Controls are provable, not promised | `aetherctl selftest` checks 15 controls against the running stack. |

## Architecture (single Docker host)

```
Browser ─TLS─► edge (Caddy) ─┬─► web (static SPA)
                             ├─► api (FastAPI) ──► postgres (RLS)   ──► temporal ◄── worker-connector ──► AWS/Azure/GCP/IBM
                             └─► keycloak (OIDC)    openbao (write-only for api) ◄──── (read-only) ┘        (only egress)
```

Only `edge` publishes ports. The `app`, `data` and `secrets` networks are internal. Details: [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) §4.

## Install

Requirements: Linux, Docker Engine ≥ 25 with Compose v2, 4 vCPU / 16 GB RAM (8 / 32 GB recommended), and outbound HTTPS for the connector.

```bash
git clone https://github.com/manojbarot1/aether-migrate.git && cd aether-migrate
cp .env.example .env          # set AETHER_PUBLIC_URL, AETHER_DOMAIN, AETHER_TLS, AETHER_ADMIN_EMAIL
deploy/scripts/bootstrap.sh   # secrets, OpenBao init/unseal/policies, stack, first admin
deploy/scripts/aetherctl selftest
```

`bootstrap.sh` prints the first administrator's temporary password (MFA is enforced at first login). It also writes the OpenBao unseal keys to `deploy/secrets/openbao-init.json`. **Distribute those keys to key holders and delete the file from the host.** See [`docs/runbooks/operations.md`](docs/runbooks/operations.md).

### Local development

```bash
cp .env.example .env
# in .env: AETHER_PUBLIC_URL=http://localhost:8580, EDGE_HTTP_PORT=8580,
#          AETHER_COMPOSE_OVERLAY=deploy/compose/compose.dev.yaml, AETHER_REQUIRE_ADMIN_MFA=false
deploy/scripts/bootstrap.sh
```

The dev overlay serves plain HTTP on `localhost` (a browser secure context), enables the API docs, and exposes the Temporal UI on `127.0.0.1:8233`.

### Demo without a cloud account

```bash
# in .env:
AETHER_COMPOSE_OVERLAY="deploy/compose/compose.dev.yaml deploy/compose/compose.demo.yaml"
```

The demo overlay starts an AWS simulator (moto) seeded with a 20-VM, 3-region estate. It includes tiered security groups, EBS volumes and an ALB. The connector is pointed at the simulator. The seed job prints a demo access key (`docker logs aether-aws-sim-seed-1`). Create an access-key connection with it, test it, and run discovery.

**Never enable the demo overlay on an installation with real connections.**

## Operate

```
deploy/scripts/aetherctl up | down | ps | logs [svc] | unseal
deploy/scripts/aetherctl selftest          # prove security controls on the live stack
deploy/scripts/aetherctl test              # lint, types, full test suite (in containers)
deploy/scripts/aetherctl backup [DIR]      # pg_dump ×4 + OpenBao Raft snapshot + checksums
deploy/scripts/aetherctl restore DIR       # verified restore (needs unseal keys)
deploy/scripts/aetherctl bao-reconfigure   # break-glass root from unseal-key quorum, re-apply policies
deploy/scripts/aetherctl sync-idp          # after changing AETHER_PUBLIC_URL
```

## Repository

```
backend/            one Python package `aether` (API, workers, domain, adapters) + tests
  src/aether/       api/ auth/ audit/ core/ db/ providers/ secrets/ workers/ workflows/
frontend/           React 19 + Vite + TypeScript SPA (OIDC PKCE, TanStack Query, Tailwind)
deploy/compose/     compose.yaml (prod), compose.dev.yaml, compose.test.yaml
deploy/config/      caddy, keycloak realm, openbao config + policies, postgres roles, temporal
deploy/scripts/     bootstrap, aetherctl, selftest, backup/restore, lint_compose
docker/             hardened multi-stage Dockerfiles
docs/               PROJECT_PLAN.md, ADRs, runbooks, review of v0.1.0
```

The import boundaries are enforced by `import-linter`: the API layer can never import cloud SDKs or provider adapters.

## Quality gates (CI)

- **Backend:** ruff, `mypy --strict`, import-linter, and pytest with coverage ≥ 80% against PostgreSQL 18.
- **Frontend:** ESLint, `tsc` strict, Vitest, and the production build.
- **Deployment config:** the Compose hardening baseline (`lint_compose.py`) and shellcheck.
- **Scanning:** gitleaks secret scanning, and Trivy on every image (fails on fixable HIGH/CRITICAL).
- **Releases:** multi-arch, with SBOM and provenance, signed keylessly with cosign.

## Security

See [SECURITY.md](SECURITY.md). Please report vulnerabilities privately.
