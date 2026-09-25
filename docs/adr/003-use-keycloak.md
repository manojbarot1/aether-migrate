# ADR-003: Use Keycloak for Identity and Access Management

**Status:** Accepted  
**Date:** 2025-01-01

## Context

AETHER MIGRATE needs a production-grade identity provider supporting OIDC, PKCE (for the SPA), MFA enforcement for sensitive roles (approver, admin), and standard RBAC via realm roles in JWT claims.

Options considered: Authentik, Ory Kratos/Hydra, Keycloak.

## Decision

Use **Keycloak 26** (quay.io/keycloak). It provides mature OIDC/SAML support, a pre-built React admin console, realm import/export for reproducible deployments, and direct PKCE support for the web SPA. MFA (TOTP) is enforced at the role level via required actions.

The realm configuration is exported to `deploy/config/keycloak/realm-export.json` and imported on first startup.

## Consequences

- **+** Battle-tested OIDC implementation with full PKCE support.
- **+** Declarative realm configuration via JSON export.
- **+** Role-based required actions enable per-role MFA enforcement.
- **−** JVM-based; higher memory footprint (~512 MB) than lighter alternatives.
- **−** Production mode (`start --optimized`) requires an optimised build step.
