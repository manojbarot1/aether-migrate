"""assistant: per-workspace settings, conversations, messages, LLM call log (RLS)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
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
        "assistant_settings",
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("egress_mode", sa.String(24), nullable=False),
        sa.Column("monthly_token_budget", sa.BigInteger),
        sa.Column("updated_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "egress_mode IN ('external_allowed','external_redacted','local_only')", name="ck_assistant_egress"
        ),
        sa.CheckConstraint(
            "egress_mode <> 'local_only' OR provider = 'ollama'", name="ck_assistant_local_only"
        ),
    )
    op.create_table(
        "conversations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("egress_mode", sa.String(24), nullable=False),
        sa.Column("redaction_vault", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("busy_until", TS),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_owner", "conversations", ["workspace_id", "user_id", "updated_at"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "conversation_id", UUID, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        # What the model saw (redacted when the workspace requires it), for faithful replay.
        sa.Column("content", pg.JSONB, nullable=False),
        # What the user sees: restored text and tool-result cards.
        sa.Column("display", pg.JSONB, nullable=False),
        sa.Column("provider", sa.String(32)),
        sa.Column("model", sa.String(128)),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conversation_id", "seq"),
        sa.CheckConstraint("role IN ('user','assistant','tool')", name="ck_message_role"),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUID, sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("egress_mode", sa.String(24), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("stop_reason", sa.String(32)),
        sa.Column("tool_calls", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("error", sa.String(200)),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_calls_ws_created", "llm_calls", ["workspace_id", "created_at"])
    for t in ("assistant_settings", "conversations", "conversation_messages", "llm_calls"):
        _rls(t)
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON assistant_settings TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON conversations TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON conversation_messages TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON llm_calls TO {APP_ROLE}")

    # Retention sweep across all workspaces. SECURITY DEFINER runs as the table owner
    # (not subject to RLS); the runtime role may only call it, not bypass RLS generally.
    op.execute(
        """
        CREATE FUNCTION aether_purge_conversations(max_age interval) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE n integer;
        BEGIN
            IF max_age < interval '1 day' THEN
                RAISE EXCEPTION 'retention must be at least one day';
            END IF;
            DELETE FROM conversations WHERE updated_at < now() - max_age;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION aether_purge_conversations(interval) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION aether_purge_conversations(interval) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS aether_purge_conversations(interval)")
    op.drop_table("llm_calls")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")
    op.drop_table("assistant_settings")
