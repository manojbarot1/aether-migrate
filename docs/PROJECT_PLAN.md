---
title: "AETHER MIGRATE — Project Plan v2"
subtitle: "AI-assisted multi-cloud migration control plane · Architecture · Security · Roadmap"
date: "2026-09-24"
---

# AETHER MIGRATE — Project Plan v2

| | |
|---|---|
| **Status** | In implementation — Phases 0–1 complete (2026-09-26); see §0.3 |
| **Version** | 2.0 (supersedes v1 from `AETHER_MIGRATE_Project_Plan.docx`) |
| **Date** | 2026-09-24 |
| **Deployment target** | Docker only (Docker Compose v2, single host for v1; images ready for multi-node later) |
| **Repository** | <https://github.com/manojbarot1/aether-migrate> |

---

## 0. Review of v1: what stays, what changes

### 0.1 Verdict

The core of v1 is sound and **stays**:

- **AI reasons, the engine executes.** This is the right boundary.
- **Read-only first.** Discovery, assessment and planning come before any execution.
- **A provider-neutral resource model.**
- **One migration path first, then expand.**

v1 falls short of "production grade" in five areas:

1. **Security of the platform itself.** The platform will hold read credentials, and later write credentials, for every cloud a customer owns. That makes it the most valuable target in their estate. v1 covers credential hygiene but not identity, RBAC, tenancy, network isolation or prompt injection.
2. **Long-running work.** Celery is fine for short jobs. It is a poor fit for migrations that run for hours or days, wait for human approval and need compensating rollback steps.
3. **Where the numbers come from.** v1 doesn't state that costs, sizes and plans must come from deterministic code and never from the LLM, or how pricing data is sourced and versioned.
4. **Order of work.** Audit logging is the last MVP step. It must be the first. The MVP sequence also builds four providers wide while §28 asks for one path end to end, so the two sections contradict each other.
5. **Operations.** v1 has no deployment topology, backups, hardening, testing strategy, non-functional requirements, risk register or exit criteria per phase.

### 0.2 Change log (v1 → v2)

| # | Change | Why |
|---|---|---|
| 1 | Audit logging, authentication and secrets move to **Phase 0/1** | Every cloud API call must be attributable from day one. Adding it later means reworking every code path. |
| 2 | The MVP is rebuilt as a **vertical slice**: AWS source → one target, end to end, then widen | Resolves the v1 §22 vs §28 conflict and proves the abstraction before copying it four times. |
| 3 | **Temporal** replaces Redis/Celery for jobs and workflows | Durable state, retries, timers, human-approval signals and saga compensation are native. The migration workflow in §16 maps onto it 1:1. |
| 4 | **OpenBao** (the open-source Vault fork) is the secret store, behind a credential boundary: only connector workers can read cloud credentials | Limits the damage if the API or AI layer is compromised. |
| 5 | LLM tools query the **inventory database**, not live cloud APIs. Tools are provider-agnostic (`inventory.search_vms`) instead of `aws.list_vms`, `azure.list_vms`, … | Fewer tools, no API rate-limit storms, answers you can repeat, and a provider abstraction that is actually used. |
| 6 | **The LLM never produces numbers or plans.** The sizing, cost, assessment and planning engines are deterministic. The model selects tools and explains results. The UI renders tool results as structured cards. | Prevents hallucinated prices or steps in a document someone will act on. |
| 7 | **Prompt-injection defences** added: tags and names from discovery are untrusted input, and mutating tools are never exposed to the model | Anyone who can set a VM tag can otherwise steer the assistant. |
| 8 | **Data-egress policy for LLMs** added: a per-workspace "local-only" mode (Ollama) plus redaction of IPs and hostnames before calls to external models | Inventory data (IPs, hostnames, tags) may be personal or confidential data under GDPR. |
| 9 | Migration paths designed as **N + M adapters** (source exporters + target importers around a canonical intermediate), not N × M pairs | 4 providers need 8 adapters instead of 12 paths. |
| 10 | Execution strategy **orchestrates the provider-native migration services** (AWS MGN, Azure Migrate, Google Migrate to Virtual Machines) where they exist, with a platform-owned cold-migration path as fallback | Block-level replication is a product in its own right. Don't rebuild it. |
| 11 | Plans become **immutable, versioned, hashed artifacts** with a generated **OpenTofu** module for the target landing zone | Engineers can review and diff the plan and apply it without our executor. That makes the product useful before Phase 9 exists. |
| 12 | A **pricing and instance catalog pipeline** is added: scheduled sync, versioned with effective dates | Pricing APIs are slow and large. Estimates must state their price date. |
| 13 | **Actual source cost** (billing APIs) sits next to list-price estimates | Comparing list price against list price ignores the discounts the customer already has (Reserved Instances, Savings Plans, EA pricing) and misleads. |
| 14 | **Quota checks** added to assessment and dry-run | Target vCPU-per-family quotas are among the most common real migration blockers. |
| 15 | **SQLite dropped.** PostgreSQL everywhere. | Everything runs in Docker anyway. Dev/prod parity, JSONB and row-level security. |
| 16 | Frontend changed from Next.js to a **React + Vite SPA** served as static files | One backend (FastAPI) instead of two. No Node server in production, so a smaller attack surface. |
| 17 | New sections: NFRs, Docker production topology, hardening, backups/DR, test strategy, CI/CD, risk register, open decisions | Needed for "production grade". |

### 0.3 Implementation status

| Phase | State | Notes |
|---|---|---|
| 0 — Foundation | **done** | Compose stack (Caddy, Keycloak, OpenBao, Temporal, Postgres 18), OIDC and RBAC, hash-chained audit, RLS, CI, backup/restore, `aetherctl selftest` |
| 1 — AWS connection | **done** | Assume-role (platform-generated ExternalId) and access-key connections, policy generator, connection test in the connector worker, excess-permission detection |
| 2a — AWS discovery | next | Port and test the v0.1.0 normalizers against recorded fixtures |
| 3–7 | planned | See §24 |

v0.1.0 (an earlier autonomous build) was reviewed and its foundation replaced; see [`REVIEW-v0.1.0.md`](REVIEW-v0.1.0.md).

---

## 1. Executive summary

AETHER MIGRATE is a self-hosted, cloud-neutral migration control plane for AWS, Microsoft Azure, Google Cloud and IBM Cloud. Engineers connect cloud accounts with least-privilege, preferably short-lived, credentials. The platform discovers infrastructure into a provider-neutral model and answers questions through a dashboard and an AI assistant. It produces deterministic, auditable outputs: target sizing, cost comparison, compatibility assessment and a versioned migration plan with generated infrastructure-as-code.

v1.0 is **read-only**: discovery, assessment, cost and planning. Controlled execution comes later, behind explicit human approval. Where possible it orchestrates each cloud's own migration service instead of reimplementing replication.

The first release targets VM-to-VM migration. The model and adapter design extend to storage, databases, networks and containers.

## 2. Product vision

**One place for an engineer to understand, cost, plan and eventually move workloads across clouds, with every number traceable to data and every action traceable to a person.**

Goals:

- Connect multiple cloud accounts safely.
- Discover and query infrastructure in natural language and through the UI.
- Normalize provider-specific resources into one model.
- Compare equivalent target configurations and costs, with assumptions shown.
- Assess compatibility and risk with explainable rules.
- Produce reviewable migration plans and IaC.
- Later, execute and validate migrations through approved, audited, reversible workflows.

**Non-goals for v1** (explicit, to prevent scope creep):

- Automatic migration of any workload.
- Application-level refactoring advice beyond flagging dependencies.
- Billing management or ongoing FinOps (a future module).
- SaaS hosting. v1 is self-hosted with Docker Compose.
- Kubernetes, database and storage migrations (see §24).

## 3. Design principles

1. **AI reasons, engines compute, adapters touch clouds.** The LLM chooses among approved tools and explains results. It never computes prices, sizes or plan steps, and it never holds credentials.
2. **Chat is a convenience, not the API.** Every capability is available through the REST API and the UI. The assistant is one client of the same services.
3. **Read-only by default.** Discovery and execution credentials are separate connections with separate secrets and separate policies.
4. **Every number has a source.** Cost figures carry the price-catalog version and date. Specs carry the inventory snapshot ID and time. Inferred values are marked as inferred.
5. **Plans are artifacts.** They are immutable once approved, content-hashed, and bound to the inventory snapshot and price catalog they came from.
6. **N + M, not N × M.** Source exporters and target importers meet at a canonical intermediate (normalized spec plus disk image).
7. **Orchestrate before you build.** Use provider-native migration services where they exist. Build only what's missing.
8. **Audit everything, log no secrets.** Every tool call, API call and approval is attributable to a person and a connection.
9. **Designed for Docker, portable beyond it.** Stateless services, configuration from environment and files, health endpoints, no host coupling.

## 4. Architecture

### 4.1 Logical view

```
                   ┌───────────────────────────────────────────────┐
  Browser ── TLS ──►  Edge proxy (Caddy/Traefik) — only public port │
                   └──────────┬───────────────────────┬────────────┘
                              │                       │
                        Web UI (static SPA)      API (FastAPI)  ◄── OIDC (Keycloak)
                                                      │
               ┌──────────────────┬───────────────────┼─────────────────────┐
               │                  │                   │                     │
         AI Orchestrator     Domain services     MCP server            Audit service
      (model gateway, tool   inventory · topology  (external clients:   (append-only,
       registry, guardrails)  sizing · cost ·        same tool registry)  hash-chained)
               │              assessment · planner
               │                  │
               └────────► Temporal (workflows: discovery, pricing sync, plan, dry-run, execute)
                                  │
                   ════════ CREDENTIAL BOUNDARY ════════
                                  │
                         Connector workers  ──► OpenBao (cloud secrets, transit keys)
                     aws · azure · gcp · ibm adapters
                                  │  egress to cloud APIs only
                         AWS    Azure    GCP    IBM Cloud
```

Key properties:

- **The credential boundary is enforced by the network and by OpenBao policy.** Only connector workers have an OpenBao role that can read `cloud-creds/*`. The API, AI orchestrator and MCP server cannot read cloud secrets, even if compromised.
- **The AI orchestrator never calls cloud APIs.** Its tools read the inventory, call the engines, or start *read-only* workflows (for example, refresh discovery).
- **Mutating workflows** (Phase 9+) start only from the UI approval flow and never from a model tool call.

### 4.2 Container view (Docker Compose)

| Service | Image basis | Networks | Egress | Stateful | Notes |
|---|---|---|---|---|---|
| `edge` | Caddy (or Traefik) | edge, app | ACME only | certs volume | Only service publishing ports (443/80) |
| `web` | nginx-unprivileged serving the SPA build | app | none | no | Static files only |
| `api` | Python 3.12 slim / distroless | app, data | none | no | FastAPI, REST + SSE for chat streaming |
| `ai` | same Python base | app, data, llm-egress | LLM endpoints only | no | Model gateway and orchestration |
| `mcp` | same Python base | app, data | none | no | MCP server (streamable HTTP, OAuth) |
| `worker-domain` | same Python base | data | none | no | Temporal worker: sizing, cost, assessment, planner |
| `worker-connector` | same Python base + cloud SDKs | data, secrets, cloud-egress | cloud API endpoints only | no | Temporal worker: the only holder of cloud credentials |
| `temporal` | temporalio/server | data | none | via Postgres | Plus `temporal-ui` on the admin network only |
| `postgres` | postgres 17 | data | none | **yes** | App DB and Temporal DB (separate databases and roles) |
| `redis` | valkey/redis | data | none | ephemeral | Cache, rate-limit buckets, SSE fan-out. Not a system of record. |
| `openbao` | openbao | secrets | none | **yes** | KV v2 for cloud credentials, Transit for envelope encryption |
| `keycloak` | keycloak | app, data | IdP federation (optional) | via Postgres | OIDC, MFA, groups → roles |
| `objstore` | Garage or SeaweedFS (S3-compatible) | data | none | **yes** | Plan exports, reports, and later disk-image staging. MinIO has restricted its community edition distribution, so check its status before choosing it. |
| `ollama` | ollama (profile `local-llm`, optional GPU) | llm-internal | none | model volume | For local-only workspaces |
| `otel-collector`, `prometheus`, `grafana`, `loki` | upstream | obs | none | **yes** | Profile `observability` |
| `migrate` | api image | data | none | no | One-shot Alembic migration job, run before `api` starts |
| `backup` | pgBackRest/wal-g + scripts | data, secrets | backup target only | no | Scheduled backups (§19.4) |

Networks are declared `internal: true` except `edge`, `llm-egress` and `cloud-egress`. Egress networks go through an egress proxy with an allow-list of domains, so a compromised container can't reach arbitrary hosts.

## 5. MVP scope (v1.0 — read-only)

### 5.1 First path

**Source: AWS EC2 → Target: Azure VMs.** This is a recommendation; the choice is open decision D1 in §27.

Why this pair:

- Azure's Retail Prices API needs no authentication and is well documented, and the Resource SKUs API reports per-region and per-zone availability and restrictions.
- AWS → Azure is the most common cross-cloud migration request, so it's the easiest to validate against real cases.
- Azure has the strictest per-family vCPU quota model, so it exercises the quota and assessment logic early.

**IBM Cloud VPC** is the second target, **GCP** the third. If IBM Cloud is the commercial priority, swap the order. The architecture doesn't change.

### 5.2 In and out of scope

| In scope for v1.0 | Out of scope for v1.0 |
|---|---|
| Discovery for AWS, Azure, GCP and IBM (added in Phase 2b after the slice works) | Any mutation of customer clouds |
| VMs, disks, NICs, subnets, VPC/VNet, security groups/firewalls, load balancers (membership only) | Databases, object storage and Kubernetes as migration subjects (discovered as dependencies only) |
| Utilization metrics where available (CPU; memory where an agent exists) | Agent installation on customer VMs |
| Sizing and cost for AWS, Azure, GCP and IBM targets | Reserved Instance/commitment purchasing advice beyond scenarios |
| Assessment rules engine | Application-level dependency mapping (network flow analysis) |
| Plan generation + OpenTofu export | Execution, cutover |
| AI assistant (read-only tools), MCP server | Multi-host HA deployment |
| OIDC login, RBAC, workspaces, audit | Multi-tenant SaaS billing |

## 6. Primary user journey

| Step | What happens | Phase |
|---|---|---|
| Connect | Admin adds a cloud connection. The platform shows the exact least-privilege policy/role to create, then tests it. | 1 |
| Discover | A scheduled or on-demand Temporal workflow fans out across regions. The UI shows progress and coverage gaps. | 2 |
| Explore | Filter the inventory in the UI or ask the assistant, e.g. "VMs over 8 vCPU / 32 GB in eu-central-1". | 2–3 |
| Inspect | Open the normalized VM view: specs, disks, NICs, security rules, tags, metrics, raw payload, snapshot time. | 2 |
| Understand | Topology graph: region → VPC → subnet → VM → disk/NIC/SG/LB. | 4 |
| Compare | Target candidates (like-for-like, right-sized, cheapest-fit) with cost scenarios and assumptions. | 5 |
| Assess | Rule results: blockers, warnings and info, each with evidence and remediation. Readiness score. | 6 |
| Plan | Deterministic plan: prerequisites, steps, downtime estimate, rollback, OpenTofu module. The AI writes a narrative summary. | 7 |
| Review / Approve | Plan versioning and diff. Four-eyes approval bound to the plan hash, with an expiry. | 7 (approval UX), 9 (enforced for execution) |
| Dry run | Permission, quota and native dry-run checks. No changes. | 8 |
| Execute / Validate | Controlled workflow with compensation. Later phase. | 9–10 |

## 7. Example AI interactions (with tool mapping)

| User says | Tool(s) the model may call | UI renders |
|---|---|---|
| "Find all my AWS VMs." | `inventory.search_vms(provider="aws")` | VM table card |
| "Show VMs larger than 8 vCPU and 32 GB." | `inventory.search_vms(min_vcpu=8, min_memory_gib=32)` | VM table card |
| "Is the inventory fresh?" | `discovery.status(connection?)` | Snapshot age and coverage card |
| "Rescan AWS production." | `discovery.refresh(connection_id)` (read-only workflow) | Progress card |
| "Show me web-prod-01's architecture." | `topology.get(resource_id, depth=2)` | Graph card |
| "What would it cost in Azure, GCP and IBM?" | `sizing.recommend` + `cost.compare` | Cost comparison card with assumptions |
| "Can I move it to IBM Cloud?" | `assessment.run(resource_id, target="ibm")` | Findings card |
| "Prepare an AWS → Azure plan." | `plan.create(resource_ids, target, options)` | Plan card and link |

Rules:

- The model's text may refer to numbers only by summarizing a tool result that is already displayed.
- Answers state the snapshot time ("as of discovery at 14:02 UTC").

## 8. Security architecture

### 8.1 Identity, access and tenancy

- **Authentication:** OIDC through Keycloak (bundled) or an external IdP. MFA required for the `approver` and `admin` roles.
- **Hierarchy:** Organization → Workspace → Cloud connections / inventory / plans. Every table carries `workspace_id`, and PostgreSQL row-level security enforces it as a second line of defence behind application checks.
- **Roles:**

  | Role | Can do |
  |---|---|
  | `viewer` | Read inventory and plans |
  | `analyst` | + run discovery, assessments and plans; use the assistant |
  | `connection-admin` | + manage cloud connections |
  | `approver` | + approve plans (cannot approve their own) |
  | `operator` | + start approved executions (Phase 9) |
  | `admin` | + workspace and user management |

- **The assistant acts as the user.** Tool authorization is checked against the requesting user's role, never against the model's choice.
- **API tokens** (for MCP and automation) are scoped, expiring and revocable, and every use is audited.

### 8.2 Cloud credentials

| Provider | Preferred (read-only) | Acceptable fallback | Minimum permissions (discovery) |
|---|---|---|---|
| AWS | IAM role assumed through STS `AssumeRole` with an **ExternalId**, from the platform's own base identity | Access key for a dedicated IAM user (rotation reminders) | Custom policy: `ec2:Describe*`, `elasticloadbalancing:Describe*`, `cloudwatch:GetMetricData`, `pricing:GetProducts`, `ce:GetCostAndUsage` (optional), `sts:GetCallerIdentity`. Not the broad `ReadOnlyAccess`, which can read S3 object data. |
| Azure | Service principal with a **certificate**. Workload identity federation if the platform exposes an OIDC issuer. | Client secret (short expiry) | `Reader` + `Monitoring Reader` (+ `Cost Management Reader` optional) at subscription or management-group scope |
| GCP | Service-account **impersonation** or Workload Identity Federation | Service-account JSON key (discouraged, flagged in the UI) | `roles/compute.viewer`, `roles/cloudasset.viewer`, `roles/monitoring.viewer`, `roles/billing.viewer` (optional) |
| IBM Cloud | Trusted profile | Service ID API key | `Viewer` on VPC Infrastructure Services; Monitoring reader (optional) |

Rules:

- **Secrets are stored in OpenBao KV v2**, one path per connection. Metadata (name, provider, scope, mode = read-only/execute, last test) lives in Postgres. The UI only shows masked values after creation.
- **Execution connections** (Phase 9) are separate records with separate paths and policies. Workers request them just in time with a short token TTL.
- **Credential material never enters** logs, traces, Temporal payloads (workflows pass `connection_id` only), LLM context, error messages or backups in plain text.
- **Connection tests** verify identity (`GetCallerIdentity` or equivalent) and, on AWS, *warn if the credential has more permissions than required*, using IAM policy simulation where available.

### 8.3 LLM security

- **Untrusted content:** resource names, tags, descriptions and OS strings come from customer clouds and may contain instructions. Tool results are passed to the model as clearly delimited data. The system prompt says so, and guardrails strip or escape suspicious content.
- **Tool allow-list per role.** No tool available to the model can mutate a cloud. The most it can do is start a read-only discovery workflow or create a *draft* plan.
- **Output handling:** model output is rendered as sanitized Markdown with no raw HTML. Links are allowed only to internal routes.
- **Data egress policy per workspace:** `external-allowed`, `external-redacted` (IPs, hostnames, account IDs and tag values replaced with reversible tokens before the call and restored in the UI), or `local-only` (Ollama).
- **Budgets:** per-workspace token and cost limits with alerting. Every LLM call is logged with model, token counts, latency and tool calls. Prompts containing customer data are not logged by default.
- **Evals:** a golden-prompt suite checks correct tool selection, no numeric hallucination and injection resistance. It runs in CI against each supported model tier (§21).

### 8.4 Privacy and compliance

- Inventory can contain personal data under GDPR (IP addresses, and names or emails in tags). The platform provides retention settings, export and deletion per workspace. Documentation lists the processors involved when external LLMs are used.
- Data retention defaults: inventory snapshots 90 days, audit events 400 days, chat transcripts 30 days. All configurable.

### 8.5 Supply chain and container hardening

- Multi-stage builds; slim or distroless runtime images; **images pinned by digest**; Renovate for updates.
- Containers run as non-root with a read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, and memory, CPU and PID limits. No Docker socket mounts anywhere.
- Secrets reach containers as **Docker secrets (files)**, not environment variables.
- CI generates an **SBOM** (Syft), **scans** it (Trivy or Grype; fails on critical findings with a fix available) and **signs** images (cosign). `pip-audit` and `npm audit` run on every PR. CodeQL and Semgrep run for static analysis.

## 9. AI layer

- **Provider abstraction:** Pydantic AI (typed tools, structured outputs, multi-provider) behind an internal `ModelGateway` interface. Supported backends: OpenAI, Anthropic, Google Gemini, OpenRouter, Ollama and OpenAI-compatible endpoints. Model IDs are configuration, never code.
- **Model tiers:** each workspace chooses `primary` and `fallback` models. A model is supported only after it passes the tool-calling eval suite. Small local models are likely to fail multi-step tool use, and the UI states the tier.
- **Tool registry:** a single Python registry of typed tools (Pydantic input and output schemas, required role, side-effect class `read`/`draft`/`mutate`). The same registry feeds:
  - the internal orchestrator (in-process calls);
  - the **MCP server** (official MCP Python SDK, streamable HTTP, OAuth 2.1). External clients such as Claude Desktop or IDEs get the same tools with the same RBAC.
- **Tool catalog (v1.0):**

  | Tool | Class |
  |---|---|
  | `connections.list` (metadata only) | read |
  | `discovery.status` | read |
  | `discovery.refresh` | read-workflow |
  | `inventory.search_vms` | read |
  | `inventory.get_resource` | read |
  | `inventory.diff_snapshots` | read |
  | `topology.get` | read |
  | `sizing.recommend` | read |
  | `cost.compare` | read |
  | `assessment.run` | read |
  | `plan.create` | draft |
  | `plan.get` | read |
  | `plan.explain` | read |
  | `plan.validate` (Phase 8) | read |

  `migration.execute` **is never a model tool.**
- **Conversation state:** stored per user and workspace, with message → tool call → result links for audit. Tool results are referenced by ID, not copied repeatedly into context.

## 10. Normalized resource model

### 10.1 Structure

- **Resource** (generic): `id` (UUID), `workspace_id`, `connection_id`, `provider`, `native_id` (ARN / Azure resource ID / GCP selfLink / IBM CRN), `account` (account / subscription / project / IBM account), `region`, `zone`, `type`, `name`, `status`, `tags` (JSONB), `created_at_source`, `snapshot_id`, `discovered_at`, `schema_version`, `raw_ref` (pointer to the stored raw payload).
- **Typed specs** in 1:1 tables (VM, Disk, NIC, Network, Subnet, SecurityGroup, LoadBalancer), not one wide table.
- **Edges** table for relationships: `(from_id, to_id, kind)` where kind is one of `attached_to`, `in_subnet`, `protected_by`, `behind_lb`, `routes_to`, `depends_on`, … The topology is a graph, not a nested list.
- **Provenance per field:** `discovered` | `derived` | `inferred` | `user_provided`, with a confidence level for inferred values (e.g. OS guessed from the image name).
- **Raw payloads** are kept (JSONB, compressed), so normalization can be re-run after bug fixes without re-discovery.

### 10.2 VM spec fields

| Group | Fields |
|---|---|
| Compute | `source_sku` (e.g. m6i.2xlarge), `vcpu`, `memory_mib`, `cpu_arch` (x86_64/arm64), `cpu_vendor`, `gpu` (model, count), `hypervisor`/virtualization type, `tenancy` (shared/dedicated host), `lifecycle` (on-demand/spot/reserved) |
| OS & boot | `os_family`, `os_distribution`, `os_version`, `os_eol_date`, `license_model` (included/BYOL/none), `boot_mode` (BIOS/UEFI), `secure_boot`, `image_ref` |
| Storage | per disk: `size_gib`, `type/class`, `iops`, `throughput_mbps`, `boot`, `encrypted`, `kms_key_ref`, `ephemeral` (instance store / temp disk: **data lost on migration**) |
| Network | per NIC: `subnet_id`, `private_ips`, `public_ips`, `security_group_ids`, `accelerated_networking`, `source_dest_check` |
| Security rules | normalized `(direction, protocol, port_from, port_to, peer_cidr \| peer_group, action, priority)` |
| Identity | attached instance role / managed identity / service account (flags cloud-specific access that will break) |
| Metrics | `cpu_p50`, `cpu_p95`, `cpu_max`, `mem_p95` (nullable), `disk_iops_p95`, `net_mbps_p95`, `window_days`, `metrics_source` |
| Cost | `actual_monthly_cost` (from billing, nullable), `list_monthly_cost` (from catalog), `currency`, `price_catalog_version` |

## 11. Discovery engine

- **Temporal workflow** per connection: list enabled regions → one child activity per region and resource type → paginate → store raw → normalize → build edges → mark snapshot complete.
- **Snapshots:** each run creates an immutable snapshot, so drift and change detection come from diffing snapshots.
- **Coverage report:** every region and resource type ends as `ok`, `denied` (with the missing permission), `disabled`, `throttled-partial` or `error`. The UI never presents partial data as complete.
- **Rate limiting:** a per-provider and per-account token bucket (in Redis), exponential backoff with jitter, and respect for `Retry-After`. Target: 1,000 VMs across 10 regions in under 10 minutes without throttling errors.
- **Metrics:** a separate, lower-frequency workflow (14–30-day window). Memory metrics usually need an in-guest agent (CloudWatch agent, Azure Monitor Agent, Ops Agent, IBM Cloud Monitoring). If none is present, sizing falls back to allocation and the result says so.
- **Provider APIs and SDKs:**

  | Provider | Inventory | Instance catalog | Metrics | Pricing | Actual cost |
  |---|---|---|---|---|---|
  | AWS | EC2 Describe* (boto3) | `DescribeInstanceTypes` | CloudWatch `GetMetricData` | Price List Query/Bulk API | Cost Explorer / CUR |
  | Azure | Resource Graph + azure-mgmt-compute/network | Resource SKUs API | Azure Monitor Metrics | Retail Prices API | Cost Management Query |
  | GCP | Cloud Asset Inventory + google-cloud-compute | `machineTypes.list` | Cloud Monitoring | Cloud Billing Catalog API | Billing export (BigQuery) |
  | IBM | VPC API (ibm-vpc SDK) | VPC instance profiles | IBM Cloud Monitoring | Global Catalog pricing | Usage Reports API |

- **Build vs. reuse:** Steampipe and CloudQuery can already pull all four clouds into SQL. They're useful for prototyping and cross-checking our adapters. Production uses our own adapters so we keep control of credential isolation, rate limiting, provenance and licensing.

## 12. Topology

- Built from the edges table. The UI uses **React Flow** with **ELK.js** auto-layout: grouped by region → VPC/VNet → subnet, with VMs, disks, NICs, SGs and LBs as nodes.
- Export to Mermaid and SVG for documents. The target-architecture view renders the plan's proposed topology with the same components, and you can switch between source and target.
- Dependencies in v1: structural only (network, SG references, LB membership, shared disks). Flow-based application mapping is future work (§24).

## 13. Sizing and cost engine

### 13.1 Catalog pipeline

- A scheduled Temporal workflow (daily) syncs instance types, their regional and zonal availability, and prices for configured regions from each provider into versioned `catalog_*` tables with `effective_from` and `catalog_version`.
- AWS bulk price files are very large. Ingest only the configured regions and product families, streaming.
- **Currency:** store prices in the provider's currency (USD), convert with a daily FX table (ECB reference rates), and display in the workspace currency (e.g. EUR or PLN) with the rate date.

### 13.2 Sizing algorithm (deterministic)

1. **Hard constraints:** CPU architecture, GPU model and count, minimum vCPU and memory (from allocation, or from p95 utilization + headroom if metrics exist), local NVMe needs, network bandwidth class, OS support, offered in the target region/zone, not restricted for the subscription.
2. **Candidate strategies:** `like_for_like` (same vCPU and memory), `right_sized` (p95 + configurable headroom, default 30%), `cheapest_fit`.
3. **Ranking:** price, then generation (prefer current), then family match (general/compute/memory ratio). Each choice records its reasoning, which the UI and the AI can quote.

### 13.3 Cost model

| Element | Notes |
|---|---|
| Compute | on-demand; 1-yr and 3-yr commitment scenarios; provider sustained-use discounts where automatic (GCP) |
| OS licence | included vs. BYOL. Windows and SQL Server portability rules differ by provider and change over time, so they're **flagged for review, never assumed** |
| Storage | per disk class mapped to capacity + IOPS + throughput. Snapshots and backup at a configurable retention. |
| Network | steady-state egress estimate (user input or metrics) + **one-time migration egress** from the source (disk GiB × source egress rate) |
| Dual-running | overlap period (default 14 days) of source and target during migration |
| Output | monthly and annual, per scenario, **as ranges where inputs are uncertain**, with the list of assumptions, catalog version and date. Labelled "estimate — not a quote". |

- **Actual cost:** where billing access is granted, show the VM's real current monthly cost next to the source list price. The difference shows the discounts the customer already has, which a target estimate must beat.

## 14. Assessment rules engine

- **Rules are code** (Python classes with metadata; a declarative YAML layer is optional later). Each rule has an `id`, `version`, `applies_to` (source, target), `severity` (blocker / warning / info), `evidence` (the fields read), `message`, `remediation` and `docs_url`. Every rule has unit tests with fixture VMs.
- **Readiness score:** `blocked` if any blocker exists; otherwise a weighted score. Findings can be *acknowledged* by a user, with a reason, and the acknowledgement is audited.
- **Starter rule catalog** (to be verified against current provider docs during Phase 6):

| ID | Check | Typical severity |
|---|---|---|
| OS-001 | OS/version supported as a guest on the target | blocker |
| OS-002 | OS end-of-life (e.g. CentOS 7, Windows Server 2012) | warning |
| CPU-001 | arm64 source: is an arm64 family available in the target region? | blocker |
| BOOT-001 | UEFI/BIOS compatibility with the target VM generation | blocker/warning |
| BOOT-002 | Boot disk > 2 TiB with MBR partitioning | blocker |
| DRV-001 | Guest drivers: NVMe/ENA → Hyper-V (Azure) or virtio (GCP/IBM KVM); Windows needs drivers injected before cutover | warning |
| DISK-001 | Ephemeral/instance-store disks hold data that will not migrate | warning |
| DISK-002 | Disk size or IOPS above the target class limit | blocker |
| NET-001 | Private IPs will change; static IP needs detected in tags/config | warning |
| NET-002 | Security rules that can't be translated 1:1 (e.g. SG-to-SG references → GCP network tags or service accounts) | warning |
| NET-003 | Public IP or DNS cutover required | info |
| ID-001 | Instance role / managed identity in use; cloud API access will break | warning |
| LIC-001 | Windows/SQL/RHEL licence model needs review | warning |
| QUOTA-001 | Target regional/family vCPU quota below requirement | blocker |
| AGENT-001 | Source-cloud agents (SSM, CloudWatch, Defender, …) need replacing | info |
| DEP-001 | Attached to a load balancer or shared storage; coordinated migration needed | warning |

## 15. Migration planning

- `plan.create` produces a **Plan** document that is deterministic given (snapshot, catalog version, options). It contains:
  - scope (VMs), target and sizing choice;
  - findings and acknowledgements;
  - prerequisites (landing zone, quotas, connectivity);
  - an ordered step list, each step with a **pre-check**, **action**, **post-check** and **compensation**;
  - downtime estimate: data size ÷ measured or assumed bandwidth, plus the cutover window;
  - the rollback plan;
  - assumptions.
- **Generated OpenTofu module** for the target: network (or references to an existing landing zone), security rules, disks and VM. Engineers can review, change and apply it themselves, which makes v1 useful before our executor exists.
- **Lifecycle:** `draft → in_review → approved → superseded | executed`. Approved plans are immutable. Each version has a SHA-256 content hash, and approvals sign `(plan_hash, approver, expiry)`. Plans can be diffed across versions.
- The AI writes the **narrative summary only**, stored separately and labelled as AI-written.
- Exports: PDF/Markdown report, JSON (schema-versioned), OpenTofu zip.

## 16. Migration execution (Phases 8–10) — strategy

### 16.1 Mechanisms per target (orchestrate first)

| Target | Preferred (low downtime, block replication) | Platform-owned fallback (cold migration) |
|---|---|---|
| AWS | AWS Application Migration Service (MGN), which accepts other clouds as sources | Snapshot → export image → `qemu-img` convert → VM Import |
| Azure | Azure Migrate (server migration, "physical" source type for other clouds) | Export → convert to fixed VHD → upload to managed disk → create VM |
| GCP | Migrate to Virtual Machines (AWS and Azure sources supported) | Export → image import |
| IBM Cloud VPC | Partner/IBM-offered migration tooling (evaluate availability) | Export → qcow2/VHD → Cloud Object Storage → custom image → VSI |

- Guest conversion for the fallback path: **virt-v2v / libguestfs** in an isolated worker container (driver injection, fstab and network config fixes). Windows needs extra handling (drivers before cutover, sysprep policy).
- The platform's job for the preferred path is orchestration and state: create and poll jobs through the provider APIs, gate each stage on approvals, and run validation.

### 16.2 Workflow (Temporal, saga pattern)

`PRE-FLIGHT (permissions, quotas, connectivity) → APPROVAL (plan hash, four-eyes, change window) → PREPARE TARGET (tofu apply) → REPLICATE / TRANSFER → TEST BOOT (isolated network) → VALIDATE (health checks, app probes) → CUTOVER APPROVAL → FINAL SYNC → CUTOVER (DNS/LB switch) → POST-VALIDATION → HYPERCARE WINDOW → CLOSE`

Any failure runs the compensation for completed steps. Rollback conditions are defined in the plan *before* approval.

### 16.3 Execution safety controls

- **The source is never modified before cutover**, apart from snapshots. **The platform never deletes source resources.** Decommissioning is a manual, out-of-band step.
- Just-in-time execution credentials with short TTLs, separate from discovery credentials.
- A global and per-workspace **kill switch**. A **concurrency limit** (blast radius), default 1 VM per workspace until explicitly raised.
- **Idempotent steps.** Every provider mutation carries a client token or idempotency key where the API supports one.
- **Dry-run:**
  - native where available: EC2 `DryRun`, Azure ARM `what-if`, `tofu plan`;
  - otherwise, permission simulation plus quota and pre-check evaluation.

## 17. Dashboard

Pages:

- **Connections** (setup wizard with a policy generator)
- **Inventory** (filter, saved views, snapshot selector)
- **Resource detail** (tabs for specs, disks, network, security, metrics, raw)
- **Topology** (source and target)
- **Compare**
- **Assessment**
- **Plans** (list, detail, diff, approve)
- **Jobs** (discovery and pricing progress, coverage)
- **Assistant** (side panel available on every page, context-aware)
- **Audit log** (filter, export)
- **Settings** (workspace, LLM policy, currency, retention)

## 18. Technology stack

| Layer | Choice | Notes / change from v1 |
|---|---|---|
| Frontend | React 19 + TypeScript + Vite, TanStack Query/Router, shadcn/ui + Tailwind, React Flow + ELK.js, Recharts | *Changed:* SPA instead of Next.js, served as static files |
| API | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic, `uv` for dependencies | Same as v1 |
| Workflows | **Temporal** (Python SDK) | *Changed:* replaces Redis/Celery |
| Database | PostgreSQL 18 (RLS, JSONB) | *Changed:* no SQLite |
| Cache / rate limiting | Redis-compatible (Valkey) | Not a queue |
| Secrets | **OpenBao** (KV v2 + Transit), Docker secrets for bootstrap | *Changed:* concrete choice |
| Identity | Keycloak (OIDC); external IdP optional | *New* |
| AI | Pydantic AI behind the `ModelGateway` interface; Ollama optional | Same intent, concrete choice |
| MCP | Official MCP Python SDK, streamable HTTP + OAuth | Same intent |
| Cloud SDKs | boto3, azure-identity + azure-mgmt-* / azure-mgmt-resourcegraph, google-cloud-compute / -asset / -monitoring, ibm-vpc + ibm-platform-services | Same |
| IaC output | OpenTofu | *New* |
| Object storage | S3-compatible (Garage or SeaweedFS) | *New* |
| Observability | OpenTelemetry → Prometheus, Loki, Grafana (Tempo optional) | Concrete |
| Edge | Caddy (automatic TLS) or Traefik | *New* |
| CI/CD | GitHub Actions, GHCR, Renovate, Syft/Trivy/cosign | *New* |

## 19. Production deployment on Docker

### 19.1 Layout

```
deploy/
  compose/
    compose.yaml               # base services
    compose.prod.yaml          # hardening, limits, restart policies
    compose.dev.yaml           # hot reload, mock clouds, seeded data
    compose.observability.yaml
    compose.local-llm.yaml     # ollama (+ GPU reservation)
  config/                      # caddy, keycloak realm export, openbao policies, otel, grafana dashboards
  scripts/                     # bootstrap, backup, restore, rotate, upgrade
```

- **Bootstrap:** `make bootstrap` runs these steps:
  1. generates Docker secrets;
  2. initializes OpenBao and prints the unseal shares *once*;
  3. creates Postgres roles and runs migrations;
  4. imports the Keycloak realm;
  5. creates the first admin.
- **OpenBao unseal:** manual unseal by default (documented runbook). Auto-unseal is optional through a cloud KMS or a second transit instance, if the operator accepts that dependency.
- **Health:** every service has a Docker `healthcheck`, and `depends_on: condition: service_healthy` orders startup. Every Python service exposes `/livez` and `/readyz`.
- **Configuration:** pydantic-settings, with secrets read from `/run/secrets/*` files.

### 19.2 Hardening checklist (enforced in CI by a compose-lint script)

- non-root `user:`, `read_only: true` + `tmpfs` where needed, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`;
- `mem_limit`, `cpus` and `pids_limit` on every service;
- no `privileged`, no host networking, no Docker socket mounts;
- only `edge` publishes ports; internal networks are `internal: true`;
- images pinned by digest; logging driver with rotation.

### 19.3 Host sizing (v1.0, up to ~5,000 VMs inventoried)

- **Minimum:** 4 vCPU, 16 GB RAM, 100 GB SSD. Add a GPU host or a remote Ollama endpoint for local-only LLM mode.
- **Recommended:** 8 vCPU, 32 GB RAM.

### 19.4 Backup and disaster recovery

| Component | Method | Target |
|---|---|---|
| Postgres (app + Temporal + Keycloak) | pgBackRest or wal-g: continuous WAL + nightly full to offsite S3-compatible storage, encrypted | RPO ≤ 15 min, RTO ≤ 2 h |
| OpenBao | Raft snapshots (encrypted) + unseal keys held offline by two people | RPO ≤ 24 h |
| Object storage | Replication or nightly sync offsite | RPO ≤ 24 h |
| Config | Git (the repo); secrets excluded | — |

- **A quarterly restore drill is part of the release checklist.** A backup nobody has restored is not a backup.

### 19.5 Upgrades and HA

- Semantic versioning. Database migrations are backward-compatible for one minor version (expand/contract). `scripts/upgrade.sh` takes a snapshot, pulls the pinned images, runs migrations, restarts and runs smoke tests.
- **HA:** Compose is single-host, so v1.0 is **not highly available**, and the documentation says so. Services are stateless and 12-factor, so a later Kubernetes/Helm or Swarm deployment needs no code changes.

## 20. Observability and audit

- **Tracing:** OpenTelemetry traces from API request → workflow → activity → cloud SDK call. Trace IDs appear in the UI on errors.
- **Metrics:**
  - discovery duration and coverage;
  - cloud API errors and throttles per provider;
  - catalog freshness;
  - LLM tokens, cost and latency per model;
  - tool-call counts and failures;
  - workflow backlog.
- **Logs:** structured JSON (structlog) with a redaction filter (known secret patterns plus OpenBao-sourced values).
- **Audit:** a separate append-only table (no update or delete grants).
  - Each event records actor, workspace, action, target, connection, tool name, redacted arguments, result status, request/trace ID and time.
  - Each row stores the hash of the previous row, so tampering is detectable.
  - The audit trail can be exported (JSON/CSV) and streamed to a SIEM through syslog or webhook.
- **Alerts** (Grafana): discovery failures, catalog older than 48 h, OpenBao sealed, backup failed, LLM budget at 80%, error-rate SLO breach.

## 21. Quality engineering

| Level | What | Tooling |
|---|---|---|
| Static | Lint, types, security lint | ruff, mypy (strict for `core`), ESLint, tsc, Semgrep, CodeQL |
| Unit | Normalizers, sizing, cost math, rules, plan generation (golden files) | pytest, hypothesis for cost/sizing invariants |
| Adapter contract | One shared test suite every provider adapter must pass | pytest + recorded fixtures (VCR-style); `moto` for AWS |
| Integration | API + DB + Temporal in Docker | pytest + testcontainers / compose |
| Live cloud (nightly) | Real sandbox accounts with a small fixed estate (tagged, budget-capped) | GitHub Actions with OIDC to each cloud; no stored keys |
| AI evals | Tool selection accuracy, refusal of mutation, injection suite, numeric-faithfulness check | pytest-based eval harness, per model tier |
| E2E | Key journeys (connect → discover → compare → plan) | Playwright |
| Performance | 5,000-VM synthetic inventory, API p95, discovery throughput | k6, synthetic data generator |
| Security | Container scan, dependency audit, DAST on staging | Trivy, pip-audit, npm audit, OWASP ZAP baseline |

**CI pipeline (GitHub Actions):** lint → unit → build images → contract + integration → SBOM/scan/sign → push to GHCR (on `main` / tags) → deploy to a staging host with Compose → E2E + smoke.

Branch protection on `main`, conventional commits, and a changelog generated by release-please.

## 22. Non-functional requirements

| Area | Requirement (v1.0) |
|---|---|
| Scale | 5,000 VMs, 50 connections, 20 concurrent users per installation |
| Discovery | 1,000 VMs across 10 regions in ≤ 10 min |
| API latency | p95 ≤ 300 ms for inventory queries; assistant first token ≤ 3 s (external model) |
| Availability | Single host; target 99.5% monthly excluding maintenance; documented restore in ≤ 2 h |
| Security | OWASP ASVS L2 for the web app; no critical/high vulnerabilities with an available fix in released images |
| Auditability | 100% of cloud API calls, tool calls, approvals and credential operations audited |
| Data freshness | Inventory age shown everywhere; price catalog ≤ 48 h old or flagged stale |
| Accessibility | WCAG 2.2 AA for the core UI |
| Portability | Runs on any Linux host with Docker Engine ≥ 25 and Compose v2; amd64 and arm64 images |

## 23. Repository structure

A single Python package keeps packaging simple. Isolation comes from separate image targets and `import-linter` contracts (ADR 0001).

```
aether-migrate/
├── backend/
│   ├── pyproject.toml, uv.lock, alembic.ini
│   ├── src/aether/
│   │   ├── api/              # FastAPI app, routers, deps (RBAC + RLS scoping)
│   │   ├── auth/             # OIDC/JWKS verification, principal, JIT provisioning
│   │   ├── audit/            # hash-chained writer + verifier
│   │   ├── core/             # domain types, enums, policies (no infrastructure imports)
│   │   ├── db/               # models, session/RLS helpers, Alembic migrations
│   │   ├── providers/        # base protocol + aws/ (azure, gcp, ibm to follow)
│   │   ├── secrets/          # OpenBao client
│   │   ├── workflows/        # Temporal workflow definitions
│   │   ├── workers/          # connector worker (credential boundary)
│   │   └── tools/            # typed tool registry (Phase 3)
│   └── tests/{unit,integration}
├── frontend/                 # React + Vite SPA
├── deploy/
│   ├── compose/              # compose.yaml (prod), compose.dev.yaml, compose.test.yaml
│   ├── config/               # caddy, keycloak, openbao, postgres, temporal
│   └── scripts/              # bootstrap, aetherctl, selftest, backup, restore, lint_compose
├── docker/                   # backend (api/connector/dev targets), web, edge Dockerfiles
├── docs/                     # this plan, adr/, runbooks/, REVIEW-v0.1.0.md
└── .github/workflows/        # ci.yml, release.yml
```

**Provider adapter interface** (`packages/providers/base`):

```python
class ProviderAdapter(Protocol):
    provider: ProviderName
    capabilities: AdapterCapabilities      # metrics? actual_cost? boot_mode? native_dry_run?

    async def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult: ...
    async def list_regions(self, ctx: ConnCtx) -> list[Region]: ...
    def discover(self, ctx: ConnCtx, region: Region, kinds: set[ResourceKind]) -> AsyncIterator[RawResource]: ...
    def normalize(self, raw: RawResource) -> NormalizedBundle: ...          # resources + edges; pure function
    async def fetch_metrics(self, ctx: ConnCtx, ids: list[str], window: Window) -> list[MetricSeries]: ...
    async def sync_catalog(self, regions: list[Region]) -> CatalogBatch: ...  # instance types + prices
    async def check_quotas(self, ctx: ConnCtx, region: Region, needs: QuotaNeeds) -> list[QuotaResult]: ...
```

`normalize` is pure, so it can be tested against fixtures with no network access. Every adapter runs the same contract suite.

## 24. Roadmap and phases (with exit criteria)

Sizes are relative effort (S ≈ 1–2 weeks, M ≈ 3–4, L ≈ 5–8 for one developer). They are for sequencing, not commitments.

| Phase | Scope | Exit criteria | Size |
|---|---|---|---|
| **0 — Foundation** | uv workspace, Dockerfiles, Compose (dev + prod profiles), Postgres + Alembic, Temporal, OpenBao, Keycloak, Caddy; OIDC login + RBAC skeleton; **audit writer**; OTel; CI (lint, test, build, scan); ADRs 001–008 | `make up` on a clean host gives a TLS-served login page. A test event lands in the hash-chained audit table. CI is green with a signed image in GHCR. | M |
| **1 — AWS connection** | Connection CRUD, OpenBao storage, STS AssumeRole + ExternalId, policy generator, connection test, least-privilege warning | A connection can be created and tested. No secret appears in logs, DB, traces or Temporal history (verified by an automated test). | S |
| **2a — AWS discovery (slice)** | Discovery workflow, snapshots, coverage, normalizer, edges, inventory API + UI, resource detail | 1,000 synthetic plus a real sandbox estate discovered. Coverage report correct for denied regions. Contract suite passes. | M |
| **3 — Assistant (read-only)** | Tool registry, Pydantic AI orchestrator, streaming chat, result cards, injection guardrails, egress modes, eval suite v1 | Eval suite passes with ≥ 95% correct tool selection on ≥ 2 model tiers. The injection suite shows zero tool misuse. | M |
| **4 — Topology** | Graph API, React Flow view, Mermaid export | The sandbox estate renders correctly, with LB, SG and subnet grouping. | S |
| **5 — Catalog, sizing & cost (Azure target)** | AWS + Azure catalog sync, FX, sizing strategies, cost scenarios, actual-cost import | Estimates within ±5% of each provider's official calculator for 20 reference configurations (documented test cases). | L |
| **6 — Assessment** | Rules engine, starter catalog, quotas, acknowledgements, readiness score | Every rule has tests. A reviewer can trace every finding to evidence. | M |
| **7 — Planner + IaC** | Plan model, lifecycle, hashing, diff, approval UX, OpenTofu for Azure, PDF/JSON export, MCP server | A generated OpenTofu module passes `tofu validate` and `tofu plan` against a sandbox subscription. **v1.0 release (read-only).** | L |
| **2b/5b — Widen providers** | Azure, GCP and IBM discovery; GCP and IBM catalogs + IaC | Contract suite passes for all four providers. Cost tolerance tests pass per provider. | L |
| **8 — Dry run** | Permission simulation, quota checks, native dry-runs, pre-flight report | Dry run catches seeded failures (missing permission, low quota) in the sandbox. | M |
| **9 — Execution (one path)** | Temporal saga, JIT execution credentials, kill switch, concurrency limits; orchestrate Azure Migrate *or* the cold path | 10 consecutive sandbox migrations of reference VMs (Linux + Windows) with automated validation and one forced rollback. | L |
| **10 — Validation & cutover** | Health probes, cutover orchestration (DNS/LB), hypercare, reports | End-to-end sandbox migration with measured downtime within the plan estimate. | M |

The order is deliberate: one complete, trustworthy vertical slice (Phases 0–7) before widening providers (2b/5b), then execution.

## 25. MVP (v1.0) success criteria

- [ ] AWS and at least one target cloud connected with read-only, least-privilege, preferably short-lived credentials. The connection test warns about excess permissions.
- [ ] VMs discovered and queryable through both the UI and natural language. Every answer shows snapshot time and coverage.
- [ ] The same VM is represented through the provider-neutral model, with provenance and the raw payload available.
- [ ] Topology shows network placement, security groups, load balancers and disks.
- [ ] Cost comparison within ±5% of official calculators for the reference set, with assumptions, catalog date and currency shown.
- [ ] Assessment findings, each with evidence and remediation.
- [ ] Versioned, hashed migration plan with a valid OpenTofu module.
- [ ] Zero mutating calls to any cloud, proven by audit and IAM policy.
- [ ] 100% of cloud API calls, tool calls and credential operations audited. No secrets in any log, trace or LLM prompt (automated test).
- [ ] Clean-host install ≤ 30 minutes with the documented bootstrap. Backup/restore drill passes.
- [ ] Assistant evals pass on at least one external and one local model tier.

## 26. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | The platform is compromised and cloud credentials leak | Low–Med | **Critical** | Credential boundary, OpenBao policies, read-only by default, short-lived credentials, egress allow-list, hardening, audit + alerts |
| R2 | Prompt injection through resource metadata | Med | High | No mutating tools for the model, delimited data, RBAC on tools, injection eval suite |
| R3 | LLM states wrong numbers or steps | Med | High | Numbers only from engines, rendered as cards; faithfulness evals |
| R4 | Pricing APIs change or estimates drift | High | Med | Versioned catalog, calculator regression tests, staleness alerts, "estimate" labelling |
| R5 | Provider API or SDK breaking changes | Med | Med | Contract tests, nightly live tests, pinned SDKs with Renovate |
| R6 | Licensing rules (Windows/SQL/RHEL) misrepresented | Med | High | Flag for review; never compute BYOL eligibility automatically in v1 |
| R7 | Scope creep (4 clouds × everything) | High | High | Vertical slice first, explicit non-goals, phase exit criteria |
| R8 | Real-cloud test costs | Med | Low | Small tagged sandbox estates, budgets/alerts, auto-teardown |
| R9 | IBM Cloud API/pricing data gaps | Med | Med | IBM as a later target; capability flags mark unsupported features |
| R10 | Single-host Compose outage | Med | Med | Documented backup/restore, RTO target, stateless services ready for multi-node |
| R11 | GDPR exposure through external LLMs | Med | High | Egress modes, redaction, retention controls, processor documentation |
| R12 | Upstream licence changes (as happened with Vault, MinIO, CloudQuery) | Med | Med | Prefer foundation-governed or OSI-licensed components; abstraction interfaces; ADR per dependency |

## 27. Open decisions (need owner input)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | First target cloud | Azure / IBM Cloud / GCP | **Azure** (§5.1), unless IBM is the commercial priority |
| D2 | Project licence (the repo is public) | Apache-2.0 / AGPL-3.0 / BSL | Apache-2.0 for adoption, or AGPL-3.0 to deter closed SaaS forks. Decide before the first external contribution. |
| D3 | Deployment model | Single-org self-hosted / multi-tenant | Single-org self-hosted for v1.0. The schema is already workspace-scoped. |
| D4 | External LLMs allowed by default? | yes / redacted / local-only | Default `external-redacted` |
| D5 | Display currency default | USD / EUR / PLN | Workspace setting, default EUR |
| D6 | Edge proxy | Caddy / Traefik | Caddy (simpler config, automatic TLS) |
| D7 | Object store | Garage / SeaweedFS / external S3 | Garage for small installs; allow external S3 |

## 28. Future expansion

- Additional sources: VMware → cloud, physical → cloud, and other clouds (OCI) as further adapters.
- Wave planning for hundreds or thousands of VMs, with dependency-aware grouping and migration factory batches.
- Application dependency mapping from VPC Flow Logs / NSG flow logs / VPC flow logs.
- Storage, database and Kubernetes migrations.
- FinOps (rightsizing, commitment optimization), DR and cloud-exit planning.
- Multi-node deployment (Helm chart) and SaaS mode.

## 29. Positioning

**AETHER MIGRATE:** a self-hosted, cloud-neutral migration control plane. Engineers discover, understand, compare, plan and eventually move workloads across AWS, Azure, Google Cloud and IBM Cloud.

The differentiator is not the chat. It is five things working together:

- a provider-neutral resource model with provenance;
- deterministic, tested discovery, cost and assessment engines;
- plans as reviewable artifacts with IaC;
- a strict credential boundary;
- orchestration of each cloud's native migration services, behind an assistant that explains but never acts on its own.

## 30. First concrete milestone (unchanged in spirit, sharpened)

On a clean Linux host, `make bootstrap && make up` brings up the platform. The user then:

1. logs in through OIDC;
2. connects AWS through an assumed role and sees "connection OK, read-only, no excess permissions";
3. asks "Show me my EC2 VMs" and gets a table with the snapshot time;
4. opens a VM and sees its normalized spec, disks, network and security rules, and topology;
5. clicks **Compare target clouds** and sees Azure candidates with cost scenarios, assumptions and catalog date;
6. runs the assessment and sees the findings;
7. generates a plan, reviews it and downloads the OpenTofu module;
8. checks the audit log, which shows every step, with zero mutating cloud calls.

Execution work starts only after this path is reliable and covered by tests.
