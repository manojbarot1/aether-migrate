# ADR-001: Use Temporal for Workflow Orchestration

**Status:** Accepted  
**Date:** 2025-01-01

## Context

AETHER MIGRATE requires durable, long-running workflows for discovery (hours), assessment (minutes), and plan execution (potentially days). Previous candidates included Celery (used in early prototypes) and Prefect.

Celery lacks durability guarantees — a worker crash loses in-flight task state. Prefect adds a managed-cloud dependency that conflicts with the self-hosted requirement.

## Decision

Use **Temporal** (open-source, self-hosted) as the workflow orchestration engine. All domain workflows (sizing, assessment, planner) run on the `domain` task queue; discovery workflows run on the `connector` task queue.

## Consequences

- **+** Durable execution: workflows survive worker restarts without state loss.
- **+** Built-in retry, timeout, and saga (compensation) patterns.
- **+** Separate task queues allow independent worker scaling.
- **−** Adds an operational dependency (Temporal server + PostgreSQL backing store).
- **−** pydantic-ai integration requires wrapping tool calls in Temporal activities.
