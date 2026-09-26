"""plans (immutable content, versioned) and plan reviews (RLS)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
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
        "plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lineage_id", UUID, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content", pg.JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("iac", pg.JSONB, nullable=False),
        sa.Column("assessment_run_id", UUID),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_display", sa.String(320)),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("submitted_at", TS),
        sa.Column("decided_at", TS),
        sa.UniqueConstraint("lineage_id", "version"),
        sa.CheckConstraint(
            "status IN ('draft','in_review','approved','rejected','superseded')", name="ck_plan_status"
        ),
    )
    op.create_index("ix_plans_ws_created", "plans", ["workspace_id", "created_at"])
    op.create_table(
        "plan_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("plan_id", UUID, sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewer_display", sa.String(320)),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS),
        sa.CheckConstraint("decision IN ('approve','reject')", name="ck_review_decision"),
    )
    # Plan content is immutable: only lifecycle columns may change after insert.
    op.execute(
        """
        CREATE FUNCTION plans_content_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.content IS DISTINCT FROM OLD.content
               OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
               OR NEW.iac IS DISTINCT FROM OLD.iac
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.lineage_id IS DISTINCT FROM OLD.lineage_id THEN
                RAISE EXCEPTION 'plan content is immutable; create a new version instead';
            END IF;
            IF OLD.status IN ('approved', 'rejected', 'superseded') AND NEW.status <> 'superseded' THEN
                RAISE EXCEPTION 'plan % is final (%)', OLD.id, OLD.status;
            END IF;
            RETURN NEW;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER plans_immutable BEFORE UPDATE ON plans FOR EACH ROW EXECUTE FUNCTION plans_content_immutable()"
    )
    for t in ("plans", "plan_reviews"):
        _rls(t)
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON plans TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON plan_reviews TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("plan_reviews")
    op.execute("DROP TRIGGER IF EXISTS plans_immutable ON plans")
    op.drop_table("plans")
    op.execute("DROP FUNCTION IF EXISTS plans_content_immutable()")
