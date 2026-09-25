"""AETHER MIGRATE AI service — FastAPI SSE streaming chat.

Endpoints:
- POST /chat          — start or continue a conversation (SSE stream)
- GET  /conversations — list conversations for the authenticated user
- GET  /conversations/{id} — conversation with message history
- DELETE /conversations/{id} — delete own conversation

Authentication is handled by the OIDC middleware (same as the API app).
For Phase 3, a simple bearer-token stub is used; production auth is wired
in the same way as the API app's auth.py.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from db.engine import get_engine
from db.models import ConversationRow, MessageRow
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tools.loader import load_all_tools
from tools.registry import CurrentUser, registry

from .egress import EgressMode
from .gateway import ModelConfig, ModelGateway
from .orchestrator import AIOrchestrator, WorkspaceAIConfig

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

_gateway: ModelGateway | None = None
_orchestrator: AIOrchestrator | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _gateway, _orchestrator, _session_factory

    engine = get_engine()
    _session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    load_all_tools(registry, _session_factory)

    _gateway = ModelGateway()
    _orchestrator = AIOrchestrator(_gateway, registry, _session_factory)

    log.info("aether_ai_started", version="0.1.0", tools=len(registry))
    yield


app = FastAPI(
    title="AETHER MIGRATE AI",
    version="0.1.0",
    docs_url="/ai/docs",
    openapi_url="/ai/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth helper (Phase 3 stub — replace with real OIDC in production)
# ---------------------------------------------------------------------------


def _get_current_user(authorization: str = Header(default="")) -> CurrentUser:
    """Extract the caller from the Authorization header.

    In Phase 3 this accepts a simple ``Bearer <encoded>`` token where the
    encoded payload is a JSON object with user fields. Production deployments
    wire in the full OIDC token validator from the API app.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    token = authorization[7:]
    try:
        import base64

        payload = json.loads(base64.b64decode(token + "==").decode())
        return CurrentUser(
            user_id=payload["user_id"],
            email=payload["email"],
            role=payload.get("role", "viewer"),
            workspace_id=payload["workspace_id"],
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _get_db_session() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    return _session_factory


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str


class ConversationOut(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str | None
    tool_name: str | None
    tool_result_id: str | None
    created_at: str


class ConversationDetailOut(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str
    messages: list[MessageOut]


# ---------------------------------------------------------------------------
# Probe endpoints
# ---------------------------------------------------------------------------


@app.get("/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@app.post("/chat")
async def chat(
    req: ChatRequest,
    user: CurrentUser = Depends(_get_current_user),
) -> StreamingResponse:
    """Stream a chat turn as Server-Sent Events.

    SSE event types:
    - ``{"type":"text","content":"..."}``
    - ``{"type":"tool_call","tool":"...","input":{...}}``
    - ``{"type":"tool_result","tool":"...","result_id":"...","card_type":"..."}``
    - ``{"type":"done","conversation_id":"...","message_id":"..."}``
    - ``{"type":"error","code":"...","message":"..."}``
    """
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not ready")

    workspace_config = WorkspaceAIConfig(
        workspace_id=user.workspace_id,
        egress_mode=EgressMode(
            os.environ.get("EGRESS_MODE", EgressMode.external_redacted.value)
        ),
        model_config=ModelConfig(
            provider=os.environ.get("LLM_PROVIDER", "openai"),  # type: ignore[arg-type]
            model_id=os.environ.get("LLM_MODEL", "gpt-4o"),
        ),
    )

    async def _event_stream() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in await _orchestrator.run(
                req.conversation_id, req.message, user, workspace_config
            ):
                event: dict[str, Any] = {"type": chunk.type}
                if chunk.content is not None:
                    event["content"] = chunk.content
                if chunk.tool_name is not None:
                    event["tool"] = chunk.tool_name
                if chunk.tool_input is not None:
                    event["input"] = chunk.tool_input
                if chunk.result_id is not None:
                    event["result_id"] = chunk.result_id
                if chunk.card_type is not None:
                    event["card_type"] = chunk.card_type
                if chunk.conversation_id is not None:
                    event["conversation_id"] = chunk.conversation_id
                if chunk.message_id is not None:
                    event["message_id"] = chunk.message_id
                if chunk.error_code is not None:
                    event["code"] = chunk.error_code
                if chunk.error_message is not None:
                    event["message"] = chunk.error_message
                yield f"data: {json.dumps(event)}\n\n".encode()
        except Exception as exc:
            log.error("chat_stream_error", error=type(exc).__name__)
            err = json.dumps({"type": "error", "code": "internal", "message": "Internal error"})
            yield f"data: {err}\n\n".encode()

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Conversation management endpoints
# ---------------------------------------------------------------------------


@app.get("/conversations")
async def list_conversations(
    user: CurrentUser = Depends(_get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(_get_db_session),
) -> list[ConversationOut]:
    async with session_factory() as db:
        result = await db.execute(
            select(ConversationRow)
            .where(
                ConversationRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
                ConversationRow.user_id == user.user_id,  # type: ignore[arg-type]
            )
            .order_by(ConversationRow.updated_at.desc())
            .limit(100)
        )
        rows = result.scalars().all()
    return [
        ConversationOut(
            id=str(r.id),
            title=r.title,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


@app.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(_get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(_get_db_session),
) -> ConversationDetailOut:
    async with session_factory() as db:
        result = await db.execute(
            select(ConversationRow).where(
                ConversationRow.id == conversation_id,  # type: ignore[arg-type]
                ConversationRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
                ConversationRow.user_id == user.user_id,  # type: ignore[arg-type]
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        msg_result = await db.execute(
            select(MessageRow)
            .where(MessageRow.conversation_id == conv.id)
            .order_by(MessageRow.created_at)
        )
        msgs = msg_result.scalars().all()

    return ConversationDetailOut(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=[
            MessageOut(
                id=str(m.id),
                role=m.role,
                content=m.content,
                tool_name=m.tool_name,
                tool_result_id=str(m.tool_result_id) if m.tool_result_id else None,
                created_at=m.created_at.isoformat(),
            )
            for m in msgs
        ],
    )


@app.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(_get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(_get_db_session),
) -> None:
    async with session_factory() as db:
        result = await db.execute(
            select(ConversationRow).where(
                ConversationRow.id == conversation_id,  # type: ignore[arg-type]
                ConversationRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
                ConversationRow.user_id == user.user_id,  # type: ignore[arg-type]
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        await db.delete(conv)
