from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aether.api.deps import PrincipalDep, SessionDep, WorkspaceContext, request_id, require_role
from aether.api.schemas import MemberOut, MembershipOut, MemberUpsert, MeOut, WorkspaceCreate, WorkspaceOut
from aether.audit.writer import AuditRecord, record
from aether.core.enums import Role
from aether.core.errors import ConflictError, NotFoundError, ValidationFailedError
from aether.db.models import Membership, User, Workspace

router = APIRouter(prefix="/api/v1", tags=["workspaces"])

RequestId = Annotated[str | None, Depends(request_id)]


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, session: SessionDep) -> MeOut:
    if principal.is_platform_admin:
        workspaces = (await session.execute(select(Workspace).order_by(Workspace.name))).scalars().all()
        memberships = [
            MembershipOut(workspace=WorkspaceOut.model_validate(w), role=Role.ADMIN) for w in workspaces
        ]
    else:
        rows = (
            await session.execute(
                select(Membership).where(Membership.user_id == principal.user_id).join(Membership.workspace)
            )
        ).scalars()
        memberships = [
            MembershipOut(workspace=WorkspaceOut.model_validate(m.workspace), role=Role(m.role)) for m in rows
        ]
    return MeOut(
        id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        is_platform_admin=principal.is_platform_admin,
        memberships=memberships,
    )


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, principal: PrincipalDep, session: SessionDep, rid: RequestId
) -> Workspace:
    principal.require_platform_admin()
    ws = Workspace(id=uuid.uuid4(), slug=body.slug, name=body.name, settings={})
    session.add(ws)
    try:
        await session.flush()
    except IntegrityError:
        raise ConflictError("a workspace with this slug already exists") from None
    await session.refresh(ws)
    await record(
        session,
        AuditRecord(
            actor=principal.actor,
            action="workspace.create",
            workspace_id=ws.id,
            target_type="workspace",
            target_id=str(ws.id),
            details={"slug": ws.slug, "name": ws.name},
            request_id=rid,
        ),
    )
    return ws


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
async def list_members(
    ctx: Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))],
) -> list[MemberOut]:
    rows = (
        await ctx.session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == ctx.workspace_id)
            .order_by(User.email)
        )
    ).all()
    return [
        MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=Role(m.role))
        for m, u in rows
    ]


@router.put("/workspaces/{workspace_id}/members", response_model=MemberOut)
async def upsert_member(
    body: MemberUpsert,
    ctx: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    rid: RequestId,
) -> MemberOut:
    """Grant a role to a user who has signed in at least once (users are provisioned on first login)."""
    user = (await ctx.session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("no user with this email has signed in yet")
    if user.id == ctx.principal.user_id and not ctx.principal.is_platform_admin:
        raise ValidationFailedError("you cannot change your own role")
    m = await ctx.session.get(Membership, (user.id, ctx.workspace_id))
    before = m.role if m else None
    if m is None:
        m = Membership(user_id=user.id, workspace_id=ctx.workspace_id, role=body.role.value)
        ctx.session.add(m)
    else:
        m.role = body.role.value
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="workspace.member.set_role",
            workspace_id=ctx.workspace_id,
            target_type="user",
            target_id=str(user.id),
            details={"email": user.email, "from": before, "to": body.role.value},
            request_id=rid,
        ),
    )
    return MemberOut(user_id=user.id, email=user.email, display_name=user.display_name, role=body.role)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    ctx: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    rid: RequestId,
) -> None:
    m = await ctx.session.get(Membership, (user_id, ctx.workspace_id))
    if m is None:
        raise NotFoundError("membership not found")
    await ctx.session.delete(m)
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="workspace.member.remove",
            workspace_id=ctx.workspace_id,
            target_type="user",
            target_id=str(user_id),
            details={"role": m.role},
            request_id=rid,
        ),
    )
