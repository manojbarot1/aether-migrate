"""Migration 006 — plans and plan_approvals tables.

Revision ID: 006
Revises: 005
Create Date: 2025-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # plans
    # ------------------------------------------------------------------
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft", "in_review", "approved", "superseded", "executed",
                name="plan_status",
            ),
            nullable=False,
            server_default="draft",
        ),
        # Scope
        sa.Column("source_resource_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("target_provider", sa.String(50), nullable=False),
        sa.Column("target_region", sa.String(100), nullable=False),
        sa.Column("sizing_strategy", sa.String(50), nullable=False),
        # Immutable content
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("catalog_version", sa.String(100), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        # Plan document
        sa.Column("plan_document", postgresql.JSONB(), nullable=False, server_default="{}"),
        # AI narrative
        sa.Column("ai_narrative", sa.Text(), nullable=True),
        sa.Column("ai_narrative_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_narrative_model", sa.String(100), nullable=True),
        # Approval
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        # Versioning
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Ownership
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
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
    op.create_index("ix_plans_workspace_id", "plans", ["workspace_id"])
    op.create_index("ix_plans_status", "plans", ["status"])
    op.create_index("ix_plans_created_at", "plans", ["created_at"])
    op.create_index("ix_plans_snapshot_id", "plans", ["snapshot_id"])

    # ------------------------------------------------------------------
    # plan_approvals
    # ------------------------------------------------------------------
    op.create_table(
        "plan_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "approver_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_plan_approvals_plan_id", "plan_approvals", ["plan_id"])
    op.create_index("ix_plan_approvals_workspace_id", "plan_approvals", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("plan_approvals")
    op.drop_index("ix_plans_snapshot_id", "plans")
    op.drop_index("ix_plans_created_at", "plans")
    op.drop_index("ix_plans_status", "plans")
    op.drop_index("ix_plans_workspace_id", "plans")
    op.drop_table("plans")
    op.execute("DROP TYPE IF EXISTS plan_status")
