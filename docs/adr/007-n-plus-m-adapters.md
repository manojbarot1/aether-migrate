# ADR-007: N+M Adapter Pattern (Not N×M)

**Status:** Accepted  
**Date:** 2025-01-01

## Context

AETHER MIGRATE supports N cloud providers (AWS, Azure, GCP, IBM) and M domain operations (discovery, sizing, cost, assessment, planner, IaC). A naïve N×M design would require each domain package to know about each provider — 4×6 = 24 integration points, each requiring updates when providers change.

## Decision

Use the **N+M adapter pattern**:

- Each of the N providers implements exactly one `ProviderAdapter` (in `packages/providers/{provider}/`).
- Each of the M domain packages depends only on the `ProviderAdapter` Protocol (from `packages/providers/base/`).
- The connector worker wires the correct adapter to the correct domain workflow at runtime.

This means adding a 5th provider (e.g. Oracle Cloud) requires only one new adapter, not 6 new integration modules.

## Consequences

- **+** Linear growth: N+M integration points instead of N×M.
- **+** Domain packages are provider-agnostic; they can be tested against mock adapters.
- **+** Contract tests in `packages/providers/base/contract_tests.py` enforce the protocol for all adapters.
- **−** The `ProviderAdapter` Protocol must be stable; breaking changes require all adapters to update.
- **−** Runtime polymorphism means type checkers cannot verify provider-specific capabilities without narrowing.
