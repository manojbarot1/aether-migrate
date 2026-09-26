# ADR 0004 — Identity in Keycloak, authorisation in the application

**Status:** accepted

**Decision.** Keycloak (realm `aether`) authenticates users: OIDC code flow with PKCE, MFA, brute-force protection. It holds exactly one authorisation fact, the `platform-admin` realm role. Workspace membership and per-workspace roles live in the application database (`memberships`), and users are provisioned just in time on first sign-in.

**Why.** Roles differ per workspace, and a user can belong to many workspaces. Encoding that in token claims (as v0.1.0 did, with one `workspace_id` claim) forces one workspace per user and ties authorisation changes to token refresh. Tokens are verified against a cached JWKS, with issuer, audience (`aether-api`) and expiry checks.
