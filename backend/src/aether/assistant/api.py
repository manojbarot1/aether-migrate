"""Assistant HTTP API (served by the `assistant` service). Chat replies stream as
server-sent events; the turn itself runs as a task so a closed browser tab never
leaves a half-executed tool loop."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from aether.api.deps import SettingsDep, WorkspaceContext, request_id, require_role
from aether.assistant.orchestrator import DEFAULT_TITLE, TurnDeps, dumps, run_turn
from aether.assistant.service import (
    effective_settings,
    provider_status,
    tokens_this_month,
    unavailable_reason,
)
from aether.audit.writer import AuditRecord, record
from aether.core.enums import Role
from aether.core.errors import AetherError, NotFoundError, ValidationFailedError
from aether.db.models import AssistantSettings, Conversation, ConversationMessage
from aether.tools.registry import available_tools

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/assistant", tags=["assistant"])
Viewer = Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))]
Admin = Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))]
RequestId = Annotated[str | None, Depends(request_id)]

EgressMode = Literal["external_allowed", "external_redacted", "local_only"]


class StatusOut(BaseModel):
    available: bool
    reason: str | None
    provider: str
    model: str
    egress_mode: str
    external: bool
    tokens_this_month: int
    monthly_token_budget: int | None
    tools: list[dict[str, str]]


@router.get("/status", response_model=StatusOut)
async def assistant_status(ctx: Viewer, settings: SettingsDep) -> StatusOut:
    eff = await effective_settings(ctx.session, settings, ctx.workspace_id)
    reason = unavailable_reason(settings, eff)
    return StatusOut(
        available=reason is None,
        reason=reason,
        provider=eff.provider,
        model=eff.model,
        egress_mode=eff.egress_mode,
        external=eff.provider != "ollama",
        tokens_this_month=await tokens_this_month(ctx.session, ctx.workspace_id),
        monthly_token_budget=eff.monthly_token_budget,
        tools=[
            {"name": t.name, "title": t.title, "side_effect": t.side_effect.value}
            for t in available_tools(ctx.principal, ctx.workspace_id)
        ],
    )


class SettingsIn(BaseModel):
    enabled: bool = True
    provider: Literal["anthropic", "ollama"]
    model: str = Field(min_length=1, max_length=128)
    egress_mode: EgressMode
    monthly_token_budget: int | None = Field(None, ge=1000)


class SettingsOut(SettingsIn):
    configured: bool
    providers: dict[str, dict[str, Any]]


@router.get("/settings", response_model=SettingsOut)
async def get_settings_(ctx: Admin, settings: SettingsDep) -> SettingsOut:
    eff = await effective_settings(ctx.session, settings, ctx.workspace_id)
    return SettingsOut(
        enabled=eff.enabled,
        provider=eff.provider,
        model=eff.model,
        egress_mode=eff.egress_mode,
        monthly_token_budget=eff.monthly_token_budget,
        configured=eff.configured,
        providers=provider_status(settings),
    )


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsIn, ctx: Admin, settings: SettingsDep, rid: RequestId) -> SettingsOut:
    providers = provider_status(settings)
    if body.egress_mode == "local_only" and body.provider != "ollama":
        raise ValidationFailedError("local_only requires the local (ollama) provider")
    if body.model not in providers[body.provider]["models"]:  # type: ignore[operator]
        raise ValidationFailedError(f"model '{body.model}' is not offered for provider '{body.provider}'")
    row = await ctx.session.get(AssistantSettings, ctx.workspace_id)
    before = (
        {"provider": row.provider, "model": row.model, "egress_mode": row.egress_mode, "enabled": row.enabled}
        if row
        else None
    )
    if row is None:
        row = AssistantSettings(workspace_id=ctx.workspace_id)
        ctx.session.add(row)
    row.enabled = body.enabled
    row.provider = body.provider
    row.model = body.model
    row.egress_mode = body.egress_mode
    row.monthly_token_budget = body.monthly_token_budget
    row.updated_by = ctx.principal.user_id
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="assistant.settings.update",
            workspace_id=ctx.workspace_id,
            target_type="workspace",
            target_id=str(ctx.workspace_id),
            details={"before": before, "after": body.model_dump()},
            request_id=rid,
        ),
    )
    await ctx.session.flush()
    return await get_settings_(ctx, settings)


# ----------------------------------------------------------------------------- conversations


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    egress_mode: str
    created_at: datetime
    updated_at: datetime


class ConversationIn(BaseModel):
    title: str | None = Field(None, max_length=200)


class MessageOut(BaseModel):
    id: uuid.UUID
    seq: int
    role: str
    display: dict[str, Any]
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


def _conv(c: Conversation) -> ConversationOut:
    return ConversationOut(
        id=c.id, title=c.title, egress_mode=c.egress_mode, created_at=c.created_at, updated_at=c.updated_at
    )


async def _own(ctx: WorkspaceContext, cid: uuid.UUID) -> Conversation:
    c = (
        await ctx.session.execute(
            select(Conversation).where(
                Conversation.id == cid,
                Conversation.workspace_id == ctx.workspace_id,
                Conversation.user_id == ctx.principal.user_id,
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise NotFoundError("conversation not found")
    return c


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(ctx: Viewer) -> list[ConversationOut]:
    rows = (
        await ctx.session.execute(
            select(Conversation)
            .where(
                Conversation.workspace_id == ctx.workspace_id, Conversation.user_id == ctx.principal.user_id
            )
            .order_by(Conversation.updated_at.desc())
            .limit(100)
        )
    ).scalars()
    return [_conv(c) for c in rows]


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationIn, ctx: Viewer, settings: SettingsDep) -> ConversationOut:
    eff = await effective_settings(ctx.session, settings, ctx.workspace_id)
    c = Conversation(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        user_id=ctx.principal.user_id,
        title=(body.title or "").strip() or DEFAULT_TITLE,
        egress_mode=eff.egress_mode,
        redaction_vault={},
    )
    ctx.session.add(c)
    await ctx.session.flush()
    await ctx.session.refresh(c)
    return _conv(c)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, ctx: Viewer) -> ConversationDetail:
    c = await _own(ctx, conversation_id)
    rows = (
        await ctx.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == c.id)
            .order_by(ConversationMessage.seq)
        )
    ).scalars()
    return ConversationDetail(
        **_conv(c).model_dump(),
        messages=[
            MessageOut(id=m.id, seq=m.seq, role=m.role, display=m.display, created_at=m.created_at)
            for m in rows
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, ctx: Viewer) -> None:
    c = await _own(ctx, conversation_id)
    await ctx.session.execute(delete(Conversation).where(Conversation.id == c.id))


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID, body: SendIn, ctx: Viewer, request: Request, rid: RequestId
) -> StreamingResponse:
    await _own(ctx, conversation_id)
    # Release the request's DB connection now; the turn opens its own short sessions.
    await ctx.session.commit()
    principal = ctx.principal
    deps: TurnDeps = request.app.state.turn_deps
    tasks: set[asyncio.Task[None]] = request.app.state.turn_tasks
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def runner() -> None:
        try:
            await run_turn(
                deps, principal, ctx.workspace_id, conversation_id, body.text, f"assistant-{rid}", emit
            )
        except AetherError as e:
            await emit("error", {"message": e.public_message, "code": e.code})
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    async def events() -> AsyncIterator[str]:
        yield dumps("start", {"conversation_id": str(conversation_id)})
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": keep-alive\n\n"  # comment line keeps proxies from idling the stream out
                continue
            if item is None:
                return
            yield dumps(*item)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
