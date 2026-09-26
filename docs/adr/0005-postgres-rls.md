# ADR 0005 — PostgreSQL only, with row-level security as the second line of defence

**Status:** accepted

**Decision.** PostgreSQL 18 is the only database. SQLite is not used, even in development. Each application database has an owner role (used only by migrations) and a runtime role (`NOSUPERUSER NOBYPASSRLS`). Workspace-scoped tables have RLS policies on `app.workspace_id`, which is set with `set_config(..., is_local => true)` at the start of every workspace-scoped transaction.

The audit table grants the runtime role INSERT/SELECT only. A trigger rejects UPDATE/DELETE/TRUNCATE for everyone.

**Consequences.** An application bug that forgets a `WHERE workspace_id = …` cannot leak data. Queries without a workspace scope see nothing.
