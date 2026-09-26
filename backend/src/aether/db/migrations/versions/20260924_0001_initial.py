"""initial schema: users, workspaces, memberships, cloud connections, audit

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aether_app"


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("is_platform_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *_ts(),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("settings", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_ts(),
    )
    op.create_table(
        "memberships",
        sa.Column(
            "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        *_ts(),
        sa.CheckConstraint(
            "role IN ('viewer','analyst','connection-admin','approver','operator','admin')",
            name="ck_membership_role",
        ),
    )
    op.create_table(
        "cloud_connections",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("auth_method", sa.String(32), nullable=False),
        sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("secret_path", sa.String(255)),
        sa.Column("secret_version", sa.Integer),
        sa.Column("status", sa.String(16), nullable=False, server_default="untested"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_result", pg.JSONB),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_ts(),
        sa.UniqueConstraint("workspace_id", "name"),
        sa.CheckConstraint("provider IN ('aws','azure','gcp','ibm')", name="ck_connection_provider"),
        sa.CheckConstraint("mode IN ('read_only','execute')", name="ck_connection_mode"),
    )
    op.create_index("ix_cloud_connections_workspace", "cloud_connections", ["workspace_id"])

    op.create_table(
        "audit_events",
        sa.Column("seq", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(255)),
        sa.Column("actor_display", sa.String(320)),
        sa.Column("workspace_id", pg.UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50)),
        sa.Column("target_id", sa.String(255)),
        sa.Column("connection_id", pg.UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("details", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("request_id", sa.String(64)),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("note", sa.Text),
    )
    op.create_index("ix_audit_workspace_seq", "audit_events", ["workspace_id", "seq"])
    op.create_index("ix_audit_occurred_at", "audit_events", ["occurred_at"])

    # --- Tamper resistance: audit rows can never be updated or deleted, by anyone
    # (including the owner) short of dropping the trigger in a migration.
    op.execute(
        """
        CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable();
        """
    )

    # --- Row-level security on workspace-scoped tables.
    op.execute("ALTER TABLE cloud_connections ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON cloud_connections
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )

    # --- Grants for the runtime role (least privilege).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users, workspaces TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON memberships, cloud_connections TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE audit_events_seq_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    op.drop_table("cloud_connections")
    op.drop_table("memberships")
    op.drop_table("workspaces")
    op.drop_table("users")
