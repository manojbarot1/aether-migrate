"""One assistant turn: user message -> model -> tools -> model ... -> answer.

Security properties, in order of importance:

1. Tools come from the registry filtered by the caller's workspace role, and each call
   is re-authorised; no tool can change a cloud.
2. Tool results are sanitised (guard) and wrapped as untrusted data before the model
   sees them; suspected injection is removed, surfaced to the user and audited.
3. In ``external_redacted`` mode everything sent to the model is redacted and
   everything coming back is restored (vault per conversation).
4. Per-turn limits: model steps, tool calls, and calls with side effects.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from temporalio.client import Client

from aether.assistant.gateway import Final, Gateway, GatewayError, ToolSpec
from aether.assistant.guard import GuardReport, envelope, error_envelope, sanitize
from aether.assistant.prompts import SYSTEM_PROMPT, turn_context
from aether.assistant.redaction import NullVault, StreamRestorer, Vault
from aether.assistant.service import (
    EffectiveSettings,
    effective_settings,
    make_gateway,
    tokens_this_month,
    unavailable_reason,
)
from aether.audit.writer import AuditRecord, record
from aether.auth.principal import Principal
from aether.config import Settings
from aether.core.enums import Role
from aether.core.errors import AetherError, ConflictError, NotFoundError
from aether.db.models import Conversation, ConversationMessage, LlmCall
from aether.db.session import workspace_scope
from aether.logging import get_logger
from aether.tools.registry import SideEffect, ToolContext, ToolOutcome, available_tools, get_tool, invoke

log = get_logger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
DEFAULT_TITLE = "New conversation"
MAX_TOOL_CALLS = 12
MAX_SIDE_EFFECTS = 2
LOCK_TTL = timedelta(minutes=10)


@dataclass(slots=True)
class TurnDeps:
    settings: Settings
    temporal: Client | None
    gateway_factory: Callable[[Settings, EffectiveSettings], Gateway] = make_gateway


def _row_to_message(m: ConversationMessage) -> dict[str, Any]:
    return {"role": m.role, **m.content}


async def _append(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: dict[str, Any],
    display: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
) -> uuid.UUID:
    async with workspace_scope(workspace_id) as s:
        seq = (
            await s.execute(
                select(func.coalesce(func.max(ConversationMessage.seq), 0)).where(
                    ConversationMessage.conversation_id == conversation_id
                )
            )
        ).scalar_one()
        row = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            seq=int(seq) + 1,
            role=role,
            content=content,
            display=display,
            provider=provider,
            model=model,
        )
        s.add(row)
    return row.id


async def _log_call(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    eff: EffectiveSettings,
    gateway: Gateway,
    final: Final | None,
    error: str | None = None,
    latency_ms: int = 0,
) -> None:
    async with workspace_scope(workspace_id) as s:
        s.add(
            LlmCall(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                user_id=user_id,
                provider=gateway.provider,
                model=final.model if final else gateway.model,
                egress_mode=eff.egress_mode,
                input_tokens=final.usage.input_tokens if final else 0,
                output_tokens=final.usage.output_tokens if final else 0,
                cache_read_tokens=final.usage.cache_read_tokens if final else 0,
                cache_write_tokens=final.usage.cache_write_tokens if final else 0,
                latency_ms=final.latency_ms if final else latency_ms,
                stop_reason=final.stop_reason if final else None,
                tool_calls=[b["name"] for b in final.blocks if b["type"] == "tool_call"] if final else [],
                error=error[:200] if error else None,
            )
        )


async def run_turn(
    deps: TurnDeps,
    principal: Principal,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_text: str,
    request_id: str | None,
    emit: Emit,
) -> None:
    settings = deps.settings
    role = principal.require(workspace_id, Role.VIEWER)

    # ---- claim the conversation (one turn at a time) and load state
    async with workspace_scope(workspace_id) as s:
        claimed = (
            await s.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == principal.user_id,
                    (Conversation.busy_until.is_(None)) | (Conversation.busy_until < func.now()),
                )
                .values(busy_until=func.now() + LOCK_TTL)
                .returning(Conversation.id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            exists = (
                await s.execute(
                    select(Conversation.id).where(
                        Conversation.id == conversation_id, Conversation.user_id == principal.user_id
                    )
                )
            ).scalar_one_or_none()
            if exists:
                raise ConflictError("a reply is already in progress")
            raise NotFoundError("conversation not found")
        conv = await s.get(Conversation, conversation_id)
        assert conv is not None
        eff = await effective_settings(s, settings, workspace_id)
        used = await tokens_this_month(s, workspace_id)
        rows = (
            (
                await s.execute(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation_id)
                    .order_by(ConversationMessage.seq)
                )
            )
            .scalars()
            .all()
        )
        history = [_row_to_message(m) for m in rows]
        vault: Vault = Vault(conv.redaction_vault) if conv.egress_mode == "external_redacted" else NullVault()
        egress_mode = conv.egress_mode
        if conv.title == DEFAULT_TITLE:
            conv.title = " ".join(user_text.split())[:80] or DEFAULT_TITLE

    gateway: Gateway | None = None
    try:
        reason = unavailable_reason(settings, eff)
        if reason is None and egress_mode != eff.egress_mode:
            # Egress is fixed per conversation so data sent under one policy never mixes with another.
            reason = "the workspace data-egress policy changed; start a new conversation"
        if reason is None and eff.monthly_token_budget is not None and used >= eff.monthly_token_budget:
            reason = "this workspace has used its monthly assistant token budget"
        if reason:
            await emit("error", {"message": reason})
            return

        gateway = deps.gateway_factory(settings, eff)
        tools = available_tools(principal, workspace_id)
        specs = [ToolSpec(t.name, t.description, t.input_schema()) for t in tools]
        now = datetime.now(UTC).isoformat(timespec="minutes")

        user_blocks = [
            {"type": "text", "text": turn_context(now, role.value, egress_mode)},
            {"type": "text", "text": vault.redact_text(user_text)},
        ]
        user_msg = {"role": "user", "blocks": user_blocks}
        await _append(workspace_id, conversation_id, "user", {"blocks": user_blocks}, {"text": user_text})
        history.append(user_msg)

        tool_ctx = ToolContext(workspace_id, principal, settings, deps.temporal, request_id, "assistant")
        total_in = total_out = 0
        tool_calls = side_effects = 0
        steps = 0
        for steps in range(1, settings.assistant_max_steps + 1):  # noqa: B007
            restorer = StreamRestorer(vault)
            final: Final | None = None
            try:
                async for ev in gateway.stream(SYSTEM_PROMPT, history, specs):
                    if isinstance(ev, Final):
                        final = ev
                    else:
                        out = restorer.feed(ev.text)
                        if out:
                            await emit("text", {"delta": out})
            except GatewayError as e:
                await _log_call(workspace_id, conversation_id, principal.user_id, eff, gateway, None, str(e))
                await emit("error", {"message": str(e)})
                return
            tail = restorer.flush()
            if tail:
                await emit("text", {"delta": tail})
            assert final is not None
            total_in += final.usage.input_tokens
            total_out += final.usage.output_tokens
            await _log_call(workspace_id, conversation_id, principal.user_id, eff, gateway, final)
            if final.notice:
                await emit("notice", {"message": final.notice})

            calls = [b for b in final.blocks if b["type"] == "tool_call"]
            parts: list[dict[str, Any]] = []
            for b in final.blocks:
                if b["type"] == "text" and b["text"]:
                    parts.append({"kind": "text", "text": vault.restore_text(b["text"])})
                elif b["type"] == "tool_call":
                    t = get_tool(b["name"])
                    parts.append(
                        {
                            "kind": "tool_call",
                            "id": b["id"],
                            "name": b["name"],
                            "title": t.title if t else b["name"],
                            "args": vault.restore(b["args"]),
                        }
                    )
            await _append(
                workspace_id,
                conversation_id,
                "assistant",
                {"blocks": final.blocks, "raw": final.raw, "provider": gateway.provider},
                {"parts": parts, "notice": final.notice, "model": final.model},
                gateway.provider,
                final.model,
            )
            history.append(
                {"role": "assistant", "blocks": final.blocks, "raw": final.raw, "provider": gateway.provider}
            )
            if not calls:
                break

            result_blocks: list[dict[str, Any]] = []
            results_display: list[dict[str, Any]] = []
            for call in calls:
                name = call["name"]
                args = vault.restore(call["args"])
                t = get_tool(name)
                allowed = t is not None and any(x.name == name for x in tools)
                await emit(
                    "tool_call",
                    {"id": call["id"], "name": name, "title": t.title if t else name, "args": args},
                )
                tool_calls += 1
                writes = t is not None and t.side_effect != SideEffect.READ
                refusal: str | None = None
                if not allowed:
                    refusal = f"tool '{name}' is not available to you"
                elif tool_calls > MAX_TOOL_CALLS:
                    refusal = "tool-call limit for this message reached; answer with what you have"
                elif writes and side_effects >= MAX_SIDE_EFFECTS:
                    refusal = "limit of actions that create work or records reached; ask the user first"
                if refusal is None:
                    side_effects += int(writes)
                    outcome = await invoke(name, tool_ctx, args)
                else:
                    outcome = ToolOutcome(False, {"error": refusal}, None, refusal)
                outcome_ok, card, err = outcome.ok, outcome.card, outcome.error

                if outcome.ok:
                    report = GuardReport()
                    safe = sanitize(outcome.model, report)
                    content = envelope(name, call["id"], vault.redact(safe), report)
                    if report.flagged:
                        await _flag(tool_ctx, name, report)
                        notice = f"Suspicious text in {name} results was removed before the model saw it."
                        await emit("notice", {"message": notice})
                else:
                    content = error_envelope(name, call["id"], err or "failed")
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "call_id": call["id"],
                        "name": name,
                        "content": content,
                        "is_error": not outcome_ok,
                    }
                )
                shown = {
                    "call_id": call["id"],
                    "name": name,
                    "ok": outcome_ok,
                    "error": err,
                    "card": card.model_dump() if card else None,
                    "duration_ms": outcome.duration_ms,
                }
                results_display.append(shown)
                await emit("tool_result", shown)

            await _append(
                workspace_id, conversation_id, "tool", {"blocks": result_blocks}, {"results": results_display}
            )
            history.append({"role": "tool", "blocks": result_blocks})
        else:
            await emit("notice", {"message": "Stopped after the maximum number of steps for one message."})

        await emit("done", {"usage": {"input_tokens": total_in, "output_tokens": total_out}, "steps": steps})
    except AetherError as e:
        await emit("error", {"message": e.public_message})
    except Exception:
        log.exception("assistant.turn_failed", conversation_id=str(conversation_id))
        await emit("error", {"message": "the assistant failed unexpectedly"})
    finally:
        close = getattr(gateway, "aclose", None)
        if close is not None:
            await close()
        async with workspace_scope(workspace_id) as s:
            values: dict[str, Any] = {"busy_until": None, "updated_at": func.now()}
            if vault.dirty:
                values["redaction_vault"] = vault.to_json()
            await s.execute(update(Conversation).where(Conversation.id == conversation_id).values(**values))


async def _flag(ctx: ToolContext, tool_name: str, report: GuardReport) -> None:
    log.warning("assistant.injection_suspected", tool=tool_name, count=report.flagged)
    async with workspace_scope(ctx.workspace_id) as s:
        await record(
            s,
            AuditRecord(
                actor=ctx.principal.actor,
                action="assistant.injection_suspected",
                workspace_id=ctx.workspace_id,
                target_type="tool",
                target_id=tool_name,
                details={"values_removed": report.flagged, "samples": [x[:80] for x in report.samples]},
                request_id=ctx.request_id,
            ),
        )


def dumps(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, separators=(',', ':'))}\n\n"
