"""Audit router — query the tamper-evident audit log."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    prev_hash: str
    event_hash: str
    workspace_id: uuid.UUID
    actor_id: uuid.UUID
    actor_email: str
    action: str
    target_type: str | None
    target_id: str | None
    connection_id: uuid.UUID | None
    tool_name: str | None
    arguments_redacted: dict[str, Any]
    result_status: str
    request_id: str | None
    trace_id: str | None
    created_at: Any


@router.get("", response_model=list[AuditEventResponse])
async def list_audit_events(
    request: Request,
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("admin")),
) -> Any:
    from db.models import AuditEventRow

    q = select(AuditEventRow).where(AuditEventRow.workspace_id == workspace_id)
    if actor_id:
        q = q.where(AuditEventRow.actor_id == actor_id)
    if action:
        q = q.where(AuditEventRow.action == action)
    if from_:
        q = q.where(AuditEventRow.created_at >= from_)
    if to:
        q = q.where(AuditEventRow.created_at <= to)
    q = q.order_by(AuditEventRow.created_at.desc()).offset(offset).limit(limit)

    result = await session.execute(q)
    rows = result.scalars().all()

    events = [
        AuditEventResponse(
            id=r.id,
            prev_hash=r.prev_hash,
            event_hash=r.event_hash,
            workspace_id=r.workspace_id,
            actor_id=r.actor_id,
            actor_email=r.actor_email,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            connection_id=r.connection_id,
            tool_name=r.tool_name,
            arguments_redacted=r.arguments_redacted,
            result_status=r.result_status,
            request_id=r.request_id,
            trace_id=r.trace_id,
            created_at=r.created_at,
        )
        for r in rows
    ]

    # Content negotiation: CSV export
    accept = request.headers.get("Accept", "application/json")
    if "text/csv" in accept:
        buf = io.StringIO()
        if events:
            writer = csv.DictWriter(buf, fieldnames=list(events[0].model_fields.keys()))
            writer.writeheader()
            for e in events:
                writer.writerow(e.model_dump())
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")

    return events
