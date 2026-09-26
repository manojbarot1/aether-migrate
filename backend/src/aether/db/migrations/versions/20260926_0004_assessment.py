"""assessment runs and finding acknowledgements (RLS)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aether_app"
UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


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
        "assessment_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("target_provider", sa.String(16), nullable=False),
        sa.Column("target_region", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("ruleset_version", sa.String(32), nullable=False),
        sa.Column("summary", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("items", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index("ix_assessment_runs_ws_created", "assessment_runs", ["workspace_id", "created_at"])
    op.create_table(
        "finding_acknowledgements",
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("native_id", sa.Text, primary_key=True),
        sa.Column("rule_id", sa.String(32), primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("acknowledged_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("acknowledged_by_display", sa.String(320)),
        sa.Column("acknowledged_at", TS, server_default=sa.func.now(), nullable=False),
    )
    for t in ("assessment_runs", "finding_acknowledgements"):
        _rls(t)
    # Runs are immutable records: insert/select only.
    op.execute(f"GRANT SELECT, INSERT ON assessment_runs TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON finding_acknowledgements TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("finding_acknowledgements")
    op.drop_table("assessment_runs")
