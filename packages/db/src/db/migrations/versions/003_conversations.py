"""Migration 003 — add AI conversation tables.

Revision ID: 003
Revises: 002
Create Date: 2025-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # conversations
    # ------------------------------------------------------------------
    op.create_table(
        "conversations",
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
        sa.Column("title", sa.String(512)),
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
    op.create_index("ix_conversations_workspace_id", "conversations", ["workspace_id"])
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    # ------------------------------------------------------------------
    # tool_results  (created before messages because messages FK → tool_results)
    # ------------------------------------------------------------------
    op.create_table(
        "tool_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "result_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "result_data",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_tool_results_conversation_id", "tool_results", ["conversation_id"])

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("tool_name", sa.String(100)),
        sa.Column("tool_call_id", sa.String(128)),
        sa.Column(
            "tool_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_results.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # ------------------------------------------------------------------
    # Row-Level Security
    # ------------------------------------------------------------------
    new_tables = ["conversations", "messages", "tool_results"]
    conn = op.get_bind()
    for table in new_tables:
        conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

        if table == "conversations":
            conn.execute(
                sa.text(
                    f"CREATE POLICY workspace_isolation ON {table} "
                    "USING (workspace_id = current_setting('app.current_workspace_id')::uuid)"
                )
            )
        else:
            # messages and tool_results inherit isolation via their conversation FK;
            # we add a policy via subquery for Belt-and-braces enforcement.
            conn.execute(
                sa.text(
                    f"CREATE POLICY workspace_isolation ON {table} "
                    "USING (conversation_id IN ("
                    "  SELECT id FROM conversations "
                    "  WHERE workspace_id = current_setting('app.current_workspace_id')::uuid"
                    "))"
                )
            )


def downgrade() -> None:
    tables = ["messages", "tool_results", "conversations"]
    for table in tables:
        op.drop_table(table)
