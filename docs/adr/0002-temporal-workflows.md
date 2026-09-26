# ADR 0002 — Temporal for all background work

**Status:** accepted

**Decision.** Discovery, pricing sync, connection tests, dry-runs and (later) execution run as Temporal workflows, on a self-hosted server backed by the shared PostgreSQL.

**Why.** Migrations run for hours or days, wait for human approval and need compensation (saga) on failure. Temporal provides durable state, retries, timers and signals natively. Celery/Redis would need all of that built by hand.

**Rules.** Workflow inputs and results carry identifiers only, never secret material. Activities that need credentials run on the `connector` task queue.
