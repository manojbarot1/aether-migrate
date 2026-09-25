"""Initial migration — create all AETHER MIGRATE tables.

Revision ID: 001
Revises: (none)
Create Date: 2025-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ENUMS
    # ------------------------------------------------------------------
    user_role = postgresql.ENUM(
        "viewer", "analyst", "connection-admin", "approver", "operator", "admin",
        name="user_role",
    )
    user_role.create(op.get_bind(), checkfirst=True)

    provider_name = postgresql.ENUM("aws", "azure", "gcp", "ibm", name="provider_name")
    provider_name.create(op.get_bind(), checkfirst=True)

    connection_mode = postgresql.ENUM("read-only", "execute", name="connection_mode")
    connection_mode.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # workspaces
    # ------------------------------------------------------------------
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("role", sa.Enum(name="user_role", create_type=False), nullable=False, server_default="viewer"),
        sa.Column("oidc_subject", sa.String(512), unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("workspace_id", "email", name="uq_users_workspace_email"),
    )
    op.create_index("ix_users_workspace_id", "users", ["workspace_id"])

    # ------------------------------------------------------------------
    # connections
    # ------------------------------------------------------------------
    op.create_table(
        "connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Enum(name="provider_name", create_type=False), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mode", sa.Enum(name="connection_mode", create_type=False), nullable=False, server_default="read-only"),
        sa.Column("scope", sa.Text()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_ok", sa.Boolean()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_connections_workspace_id", "connections", ["workspace_id"])

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Enum(name="provider_name", create_type=False), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("coverage", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_snapshots_workspace_id", "snapshots", ["workspace_id"])
    op.create_index("ix_snapshots_connection_id", "snapshots", ["connection_id"])

    # ------------------------------------------------------------------
    # resources
    # ------------------------------------------------------------------
    op.create_table(
        "resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column("provider", sa.Enum(name="provider_name", create_type=False), nullable=False),
        sa.Column("native_id", sa.String(512), nullable=False),
        sa.Column("account", sa.String(255), nullable=False),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("zone", sa.String(100)),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("name", sa.String(512)),
        sa.Column("status", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("spec", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("raw_ref", sa.Text()),
        sa.Column("created_at_source", sa.DateTime(timezone=True)),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(20), nullable=False, server_default="1.0"),
        sa.UniqueConstraint(
            "workspace_id", "connection_id", "snapshot_id", "native_id",
            name="uq_resources_native_id",
        ),
    )
    op.create_index("ix_resources_workspace_id", "resources", ["workspace_id"])
    op.create_index("ix_resources_snapshot_id", "resources", ["snapshot_id"])
    op.create_index("ix_resources_provider_region", "resources", ["provider", "region"])
    op.create_index("ix_resources_kind", "resources", ["kind"])

    # ------------------------------------------------------------------
    # resource_edges
    # ------------------------------------------------------------------
    op.create_table(
        "resource_edges",
        sa.Column(
            "from_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "to_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(50), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snapshots.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_resource_edges_workspace_id", "resource_edges", ["workspace_id"])

    # ------------------------------------------------------------------
    # audit_events
    # ------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_email", sa.String(320), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100)),
        sa.Column("target_id", sa.String(512)),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True)),
        sa.Column("tool_name", sa.String(100)),
        sa.Column(
            "arguments_redacted",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("result_status", sa.String(50), nullable=False, server_default="success"),
        sa.Column("request_id", sa.String(128)),
        sa.Column("trace_id", sa.String(128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_events_workspace_id", "audit_events", ["workspace_id"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])

    # ------------------------------------------------------------------
    # api_tokens
    # ------------------------------------------------------------------
    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("scope", sa.Text()),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_api_tokens_workspace_id", "api_tokens", ["workspace_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])

    # ------------------------------------------------------------------
    # Row-Level Security policies
    # NOTE: RLS is enabled on all tables. Application sets the
    # `app.current_workspace_id` session variable before executing queries.
    # Superuser connections (migrations, admin tasks) bypass RLS.
    # ------------------------------------------------------------------
    tables_with_rls = [
        "workspaces",
        "users",
        "connections",
        "snapshots",
        "resources",
        "resource_edges",
        "audit_events",
        "api_tokens",
    ]

    conn = op.get_bind()
    for table in tables_with_rls:
        conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

        if table == "workspaces":
            conn.execute(
                sa.text(
                    f"CREATE POLICY workspace_isolation ON {table} "
                    "USING (id = current_setting('app.current_workspace_id')::uuid)"
                )
            )
        else:
            conn.execute(
                sa.text(
                    f"CREATE POLICY workspace_isolation ON {table} "
                    "USING (workspace_id = current_setting('app.current_workspace_id')::uuid)"
                )
            )


def downgrade() -> None:
    tables = [
        "api_tokens",
        "audit_events",
        "resource_edges",
        "resources",
        "snapshots",
        "connections",
        "users",
        "workspaces",
    ]
    for table in tables:
        op.drop_table(table)

    conn = op.get_bind()
    for enum_name in ("user_role", "provider_name", "connection_mode"):
        conn.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
