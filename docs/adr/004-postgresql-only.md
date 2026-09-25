# ADR-004: PostgreSQL Only (No SQLite)

**Status:** Accepted  
**Date:** 2025-01-01

## Context

An early prototype used SQLite for simplicity. However, AETHER MIGRATE requires Row-Level Security (RLS) for multi-tenant workspace isolation, JSONB for flexible resource spec storage, async drivers (`asyncpg`), and a database that Temporal itself supports as a backend store.

## Decision

Use **PostgreSQL 17** exclusively. SQLite is not supported even for local development — `docker compose up` starts a Postgres container automatically, so the developer experience overhead is minimal.

RLS policies are applied in the initial migration (`001_initial.py`), enforcing `workspace_id` isolation at the database layer in addition to the application-level checks.

## Consequences

- **+** RLS provides defence-in-depth against workspace data leaks.
- **+** JSONB enables flexible `spec` and `provenance` storage without schema migrations for every provider field.
- **+** Single database for app data and Temporal backing store simplifies operations.
- **−** Slightly higher local dev friction (no single-file DB).
- **−** All developers need Docker to run the test suite with a real database.
