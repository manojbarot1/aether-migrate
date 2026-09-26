from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from aether.api.deps import PrincipalDep, SessionDep, WorkspaceContext, require_role
from aether.api.schemas import AuditEventOut, AuditPage, ChainVerificationOut
from aether.audit.writer import verify_chain
from aether.core.enums import Role
from aether.db.models import AuditEvent

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/workspaces/{workspace_id}/audit", response_model=AuditPage)
async def list_audit(
    ctx: Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[int | None, Query(description="Return events with seq lower than this")] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
) -> AuditPage:
    q = select(AuditEvent).where(AuditEvent.workspace_id == ctx.workspace_id)
    if before is not None:
        q = q.where(AuditEvent.seq < before)
    if action:
        q = q.where(AuditEvent.action.startswith(action))
    rows = (await ctx.session.execute(q.order_by(AuditEvent.seq.desc()).limit(limit + 1))).scalars().all()
    items = [AuditEventOut.model_validate(r) for r in rows[:limit]]
    return AuditPage(items=items, next_before=items[-1].seq if len(rows) > limit else None)


@router.get("/audit/verify", response_model=ChainVerificationOut)
async def verify(principal: PrincipalDep, session: SessionDep) -> ChainVerificationOut:
    principal.require_platform_admin()
    v = await verify_chain(session)
    return ChainVerificationOut(ok=v.ok, checked=v.checked, first_bad_seq=v.first_bad_seq, reason=v.reason)
