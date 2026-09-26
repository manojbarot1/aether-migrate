"""inventory: snapshots, resources, resource_edges (RLS)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aether_app"
UUID = pg.UUID(as_uuid=True)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY workspace_isolation ON {table}
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "connection_id", UUID, sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("workflow_id", sa.String(255)),
        sa.Column("requested_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("regions", pg.JSONB),
        sa.Column("coverage", pg.JSONB),
        sa.Column("stats", pg.JSONB),
        sa.Column("error", sa.Text),
        sa.CheckConstraint("status IN ('running','complete','partial','failed')", name="ck_snapshot_status"),
    )
    op.create_index(
        "ix_snapshots_ws_conn_started", "snapshots", ["workspace_id", "connection_id", "started_at"]
    )

    op.create_table(
        "resources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot_id", UUID, sa.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "connection_id", UUID, sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("native_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("zone", sa.String(64)),
        sa.Column("status", sa.String(32)),
        sa.Column("tags", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("spec", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("vcpu", sa.Integer),
        sa.Column("memory_mib", sa.Integer),
        sa.Column("cpu_arch", sa.String(16)),
        sa.Column("os_family", sa.String(16)),
        sa.Column("created_at_source", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "native_id"),
    )
    op.create_index("ix_resources_ws_snapshot_type", "resources", ["workspace_id", "snapshot_id", "type"])
    op.create_index("ix_resources_tags", "resources", ["tags"], postgresql_using="gin")

    op.create_table(
        "resource_edges",
        sa.Column("snapshot_id", UUID, sa.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("from_id", UUID, sa.ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("to_id", UUID, sa.ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
    )
    op.create_index("ix_edges_to", "resource_edges", ["to_id"])

    for t in ("snapshots", "resources", "resource_edges"):
        _rls(t)
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON snapshots TO {APP_ROLE}")
    # Resources and edges are written once per snapshot and never updated; deletion
    # happens only through snapshot retention (CASCADE).
    op.execute(f"GRANT SELECT, INSERT ON resources, resource_edges TO {APP_ROLE}")
    op.execute(f"GRANT DELETE ON snapshots TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("resource_edges")
    op.drop_table("resources")
    op.drop_table("snapshots")
