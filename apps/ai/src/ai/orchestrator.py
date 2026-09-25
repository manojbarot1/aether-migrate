"""AIOrchestrator — conversation flow, tool execution, and message storage.

The orchestrator is the central coordinator for a streaming chat turn:
1. Load conversation history from the database.
2. Build the system prompt.
3. Apply EgressGuard to all messages before sending to the model.
4. Stream from ModelGateway.
5. On tool call: authorize → execute → store → continue.
6. Store the completed assistant message in the database.
7. Yield ChatChunk objects to the caller (FastAPI SSE endpoint).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog
from db.models import ConversationRow, MessageRow, ToolResultRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tools.registry import CurrentUser, ToolDefinition, ToolRegistry

from .egress import EgressGuard, EgressMode
from .gateway import ChatChunk, ChatMessage, ModelConfig, ModelGateway, ToolSpec
from .guardrails import GuardrailEngine
from .prompts import build_system_prompt

log = structlog.get_logger(__name__)

# How many previous messages to load as context
_HISTORY_LIMIT = 20

# Card types by tool name
_TOOL_CARD_TYPES: dict[str, str] = {
    "inventory.search_vms": "vm_table",
    "inventory.get_resource": "vm_table",
    "inventory.diff_snapshots": "vm_table",
    "discovery.status": "snapshot_status",
    "discovery.refresh": "snapshot_status",
    "topology.get": "topology_preview",
    "plan.create": "plan_summary",
    "plan.get": "plan_summary",
    "plan.explain": "plan_summary",
}


@dataclass
class WorkspaceAIConfig:
    """Per-workspace AI configuration."""

    workspace_id: str
    workspace_name: str | None = None
    egress_mode: EgressMode = EgressMode.external_redacted
    model_config: ModelConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.model_config is None:
            self.model_config = ModelConfig()


class AIOrchestrator:
    """Coordinate a single chat turn end-to-end."""

    def __init__(
        self,
        gateway: ModelGateway,
        registry: ToolRegistry,
        db_session_factory: Any,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._db_factory = db_session_factory
        self._egress = EgressGuard()
        self._guardrails = GuardrailEngine()

    async def run(
        self,
        conversation_id: str | None,
        user_message: str,
        user: CurrentUser,
        workspace_config: WorkspaceAIConfig,
    ) -> AsyncIterator[ChatChunk]:
        """Execute a full chat turn and yield streaming chunks."""
        return self._run_inner(conversation_id, user_message, user, workspace_config)

    async def _run_inner(
        self,
        conversation_id: str | None,
        user_message: str,
        user: CurrentUser,
        workspace_config: WorkspaceAIConfig,
    ) -> AsyncIterator[ChatChunk]:
        async with self._db_factory() as db:
            # ----------------------------------------------------------
            # 1. Resolve or create the conversation
            # ----------------------------------------------------------
            conv = await self._get_or_create_conversation(
                db, conversation_id, user, user_message, workspace_config
            )
            conv_id = str(conv.id)

            # ----------------------------------------------------------
            # 2. Store the user message
            # ----------------------------------------------------------
            user_msg_row = MessageRow(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                role="user",
                content=user_message,
            )
            db.add(user_msg_row)
            await db.flush()

            # ----------------------------------------------------------
            # 3. Build the message list to send to the model
            # ----------------------------------------------------------
            history = await self._load_history(db, conv.id)
            messages: list[ChatMessage] = [
                ChatMessage(
                    role="system",
                    content=build_system_prompt(workspace_config.workspace_name),
                ),
                *history,
                ChatMessage(role="user", content=user_message),
            ]

            # ----------------------------------------------------------
            # 4. Apply egress guard
            # ----------------------------------------------------------
            raw_dicts = [{"role": m.role, "content": m.content or ""} for m in messages]
            redacted_dicts = self._egress.apply(
                raw_dicts,
                workspace_config.egress_mode,
                conv_id,
                provider=workspace_config.model_config.provider,
            )
            redacted_messages = [
                ChatMessage(role=d["role"], content=d["content"])
                for d in redacted_dicts
            ]

            # ----------------------------------------------------------
            # 5. Build tool specs for the role
            # ----------------------------------------------------------
            available_tools = self._registry.list_for_role(user.role)
            tool_specs = [_tool_def_to_spec(t) for t in available_tools]

            # ----------------------------------------------------------
            # 6. Stream from the model, handle tool calls
            # ----------------------------------------------------------
            assistant_content_parts: list[str] = []
            async for chunk in self._gateway.chat_stream(
                redacted_messages, tool_specs, workspace_config.model_config
            ):
                if chunk.type == "text":
                    safe = self._guardrails.check_model_output(chunk.content or "")
                    assistant_content_parts.append(safe)
                    yield ChatChunk(type="text", content=safe)

                elif chunk.type == "tool_call":
                    tool_name = chunk.tool_name or ""
                    tool_input = chunk.tool_input or {}

                    yield ChatChunk(
                        type="tool_call",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )

                    # Execute the tool
                    result_id, result_dict = await self._execute_and_store_tool(
                        db=db,
                        conv_id=conv.id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        user=user,
                    )
                    card_type = _TOOL_CARD_TYPES.get(tool_name, "generic")
                    yield ChatChunk(
                        type="tool_result",
                        tool_name=tool_name,
                        result_id=result_id,
                        card_type=card_type,
                    )

                    # Append the guarded result as assistant context note
                    guarded = self._guardrails.check_tool_result(tool_name, result_dict)
                    assistant_content_parts.append(
                        f"\n[tool:{tool_name} result_id:{result_id}]\n"
                        + json.dumps(guarded, default=str)
                    )

                elif chunk.type == "error":
                    yield chunk
                    return

            # ----------------------------------------------------------
            # 7. Store the assistant message
            # ----------------------------------------------------------
            assistant_content = "".join(assistant_content_parts)
            asst_msg_row = MessageRow(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                role="assistant",
                content=assistant_content,
            )
            db.add(asst_msg_row)
            await db.flush()

            yield ChatChunk(
                type="done",
                conversation_id=conv_id,
                message_id=str(asst_msg_row.id),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_or_create_conversation(
        self,
        db: AsyncSession,
        conversation_id: str | None,
        user: CurrentUser,
        first_message: str,
        workspace_config: WorkspaceAIConfig,
    ) -> ConversationRow:
        if conversation_id:
            result = await db.execute(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id,  # type: ignore[arg-type]
                    ConversationRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
                )
            )
            conv = result.scalar_one_or_none()
            if conv:
                return conv

        # Create a new conversation; use the first 80 chars as the title
        title = first_message[:80]
        conv = ConversationRow(
            id=uuid.uuid4(),
            workspace_id=user.workspace_id,  # type: ignore[arg-type]
            user_id=user.user_id,  # type: ignore[arg-type]
            title=title,
        )
        db.add(conv)
        await db.flush()
        return conv

    async def _load_history(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> list[ChatMessage]:
        result = await db.execute(
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at.desc())
            .limit(_HISTORY_LIMIT)
        )
        rows = list(reversed(result.scalars().all()))
        return [
            ChatMessage(
                role=row.role,  # type: ignore[arg-type]
                content=row.content or "",
                tool_name=row.tool_name,
                tool_call_id=row.tool_call_id,
            )
            for row in rows
        ]

    async def _execute_and_store_tool(
        self,
        db: AsyncSession,
        conv_id: uuid.UUID,
        tool_name: str,
        tool_input: dict[str, Any],
        user: CurrentUser,
    ) -> tuple[str, dict[str, Any]]:
        """Run the tool, store the result, return (result_id, result_dict)."""
        # Log only tool name + status — not the raw input (may contain PII)
        try:
            result_dict = await self._registry.execute(tool_name, tool_input, user, db)
            status = "success"
        except NotImplementedError as exc:
            result_dict = {"error": str(exc), "code": "not_implemented"}
            status = "not_implemented"
        except Exception as exc:
            log.warning("tool_execution_failed", tool=tool_name, error=type(exc).__name__)
            result_dict = {"error": "Tool execution failed", "code": "tool_error"}
            status = "error"

        log.info("tool_executed", tool=tool_name, status=status)

        # Compute a stable hash of the input for deduplication / audit
        input_hash = hashlib.sha256(
            json.dumps(tool_input, sort_keys=True, default=str).encode()
        ).hexdigest()

        # Extract snapshot_id from result if available (for tool_results FK)
        snapshot_id: str | None = None
        if isinstance(result_dict, dict):
            snapshot_id = (
                result_dict.get("snapshot_id")
                or result_dict.get("snapshot_time")  # fallback
            )

        result_row = ToolResultRow(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            tool_name=tool_name,
            input_hash=input_hash,
            result_snapshot_id=snapshot_id,  # type: ignore[arg-type]
            result_data=result_dict,
        )
        db.add(result_row)
        await db.flush()

        return str(result_row.id), result_dict


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _tool_def_to_spec(tool: ToolDefinition) -> ToolSpec:
    """Convert a ToolDefinition to the minimal ToolSpec for the LLM."""
    try:
        schema = tool.input_schema.model_json_schema()
    except Exception:
        schema = {}
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters=schema,
    )
