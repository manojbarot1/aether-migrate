"""Migration plans: create from an assessment, review (four-eyes), revise, export."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from aether.api.deps import WorkspaceContext, request_id, require_role
from aether.audit.writer import AuditRecord, record
from aether.core.enums import Role
from aether.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from aether.db.models import Plan, PlanReview
from aether.planner.engine import PlanContent, PlanOptions, to_markdown
from aether.planner.service import generate

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/plans", tags=["plans"])
Viewer = Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))]
Analyst = Annotated[WorkspaceContext, Depends(require_role(Role.ANALYST))]
Approver = Annotated[WorkspaceContext, Depends(require_role(Role.APPROVER))]
RequestId = Annotated[str | None, Depends(request_id)]

APPROVAL_VALIDITY = timedelta(days=14)


class CreatePlan(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    assessment_run_id: uuid.UUID
    options: PlanOptions = Field(default_factory=PlanOptions)


class RevisePlan(BaseModel):
    assessment_run_id: uuid.UUID | None = None
    options: PlanOptions | None = None


class ReviewBody(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(min_length=3, max_length=4000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="the hash the reviewer read")


class ReviewOut(BaseModel):
    reviewer_display: str | None
    decision: str
    comment: str
    content_hash: str
    created_at: datetime
    expires_at: datetime | None


class PlanSummary(BaseModel):
    id: uuid.UUID
    lineage_id: uuid.UUID
    version: int
    name: str
    status: str
    content_hash: str
    created_by_display: str | None
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    totals: dict[str, Any]


class PlanOut(PlanSummary):
    content: dict[str, Any]
    iac_files: list[str]
    iac_notes: list[str]
    reviews: list[ReviewOut]
    versions: list[dict[str, Any]]


def _summary(p: Plan) -> PlanSummary:
    return PlanSummary(
        id=p.id,
        lineage_id=p.lineage_id,
        version=p.version,
        name=p.name,
        status=p.status,
        content_hash=p.content_hash,
        created_by_display=p.created_by_display,
        created_at=p.created_at,
        submitted_at=p.submitted_at,
        decided_at=p.decided_at,
        totals=p.content.get("totals", {}),
    )


async def _get(ctx: WorkspaceContext, plan_id: uuid.UUID) -> Plan:
    p = await ctx.session.get(Plan, plan_id)
    if p is None or p.workspace_id != ctx.workspace_id:
        raise NotFoundError("plan not found")
    return p


async def _full(ctx: WorkspaceContext, p: Plan) -> PlanOut:
    reviews = (
        (
            await ctx.session.execute(
                select(PlanReview).where(PlanReview.plan_id == p.id).order_by(PlanReview.created_at)
            )
        )
        .scalars()
        .all()
    )
    versions = (
        await ctx.session.execute(
            select(Plan.id, Plan.version, Plan.status, Plan.created_at)
            .where(Plan.lineage_id == p.lineage_id)
            .order_by(Plan.version)
        )
    ).all()
    return PlanOut(
        **_summary(p).model_dump(),
        content=p.content,
        iac_files=sorted(p.iac.get("files", {})),
        iac_notes=p.iac.get("notes", []),
        reviews=[
            ReviewOut(
                reviewer_display=r.reviewer_display,
                decision=r.decision,
                comment=r.comment,
                content_hash=r.content_hash,
                created_at=r.created_at,
                expires_at=r.expires_at,
            )
            for r in reviews
        ],
        versions=[
            {"id": str(i), "version": v, "status": s, "created_at": c.isoformat()} for i, v, s, c in versions
        ],
    )


async def _audit(ctx: WorkspaceContext, action: str, p: Plan, rid: str | None, **details: Any) -> None:
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action=action,
            workspace_id=ctx.workspace_id,
            target_type="plan",
            target_id=str(p.id),
            details={"name": p.name, "version": p.version, "content_hash": p.content_hash, **details},
            request_id=rid,
        ),
    )


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: CreatePlan, ctx: Analyst, rid: RequestId) -> PlanOut:
    content, digest, iac = await generate(ctx.session, body.assessment_run_id, body.name, body.options)
    p = Plan(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        lineage_id=uuid.uuid4(),
        version=1,
        name=body.name,
        status="draft",
        content=content.model_dump(mode="json"),
        content_hash=digest,
        iac=iac,
        assessment_run_id=body.assessment_run_id,
        created_by=ctx.principal.user_id,
        created_by_display=ctx.principal.actor.display,
    )
    ctx.session.add(p)
    await ctx.session.flush()
    await _audit(ctx, "plan.create", p, rid, vms=content.totals["vms"], waves=content.totals["waves"])
    await ctx.session.refresh(p)
    return await _full(ctx, p)


@router.get("", response_model=list[PlanSummary])
async def list_plans(ctx: Viewer) -> list[PlanSummary]:
    rows = (
        await ctx.session.execute(
            select(Plan)
            .where(Plan.workspace_id == ctx.workspace_id)
            .order_by(Plan.created_at.desc())
            .limit(200)
        )
    ).scalars()
    return [_summary(p) for p in rows]


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: uuid.UUID, ctx: Viewer) -> PlanOut:
    return await _full(ctx, await _get(ctx, plan_id))


@router.post("/{plan_id}/submit", response_model=PlanOut)
async def submit(plan_id: uuid.UUID, ctx: Analyst, rid: RequestId) -> PlanOut:
    p = await _get(ctx, plan_id)
    if p.status != "draft":
        raise ConflictError(f"only drafts can be submitted (plan is {p.status})")
    if p.content.get("totals", {}).get("open_blockers"):
        raise ValidationFailedError("the plan contains machines with unresolved blockers")
    p.status, p.submitted_at = "in_review", datetime.now(UTC)
    await _audit(ctx, "plan.submit", p, rid)
    return await _full(ctx, p)


@router.post("/{plan_id}/review", response_model=PlanOut)
async def review(plan_id: uuid.UUID, body: ReviewBody, ctx: Approver, rid: RequestId) -> PlanOut:
    """Four-eyes review bound to the content hash the reviewer read."""
    p = await _get(ctx, plan_id)
    if p.status != "in_review":
        raise ConflictError(f"plan is {p.status}, not in review")
    if p.created_by == ctx.principal.user_id:
        raise ForbiddenError("authors cannot review their own plan")
    if body.content_hash != p.content_hash:
        raise ConflictError("the plan changed since you opened it; reload and review again")
    now = datetime.now(UTC)
    ctx.session.add(
        PlanReview(
            plan_id=p.id,
            workspace_id=ctx.workspace_id,
            reviewer_id=ctx.principal.user_id,
            reviewer_display=ctx.principal.actor.display,
            decision=body.decision,
            comment=body.comment,
            content_hash=p.content_hash,
            expires_at=now + APPROVAL_VALIDITY if body.decision == "approve" else None,
        )
    )
    p.status, p.decided_at = ("approved" if body.decision == "approve" else "rejected"), now
    await _audit(ctx, f"plan.{body.decision}", p, rid, comment=body.comment)
    return await _full(ctx, p)


@router.post("/{plan_id}/revise", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def revise(plan_id: uuid.UUID, body: RevisePlan, ctx: Analyst, rid: RequestId) -> PlanOut:
    old = await _get(ctx, plan_id)
    latest = (
        await ctx.session.execute(select(func.max(Plan.version)).where(Plan.lineage_id == old.lineage_id))
    ).scalar_one()
    if old.version != latest:
        raise ConflictError("only the latest version can be revised")
    run_id = body.assessment_run_id or old.assessment_run_id
    if run_id is None:
        raise ValidationFailedError("an assessment run is required")
    options = body.options or PlanOptions.model_validate(old.content["options"])
    content, digest, iac = await generate(ctx.session, run_id, old.name, options)
    p = Plan(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        lineage_id=old.lineage_id,
        version=old.version + 1,
        name=old.name,
        status="draft",
        content=content.model_dump(mode="json"),
        content_hash=digest,
        iac=iac,
        assessment_run_id=run_id,
        created_by=ctx.principal.user_id,
        created_by_display=ctx.principal.actor.display,
    )
    old.status = "superseded"
    ctx.session.add(p)
    await ctx.session.flush()
    await _audit(ctx, "plan.revise", p, rid, previous=str(old.id))
    await ctx.session.refresh(p)
    return await _full(ctx, p)


@router.get("/{plan_id}/diff/{other_id}")
async def diff(plan_id: uuid.UUID, other_id: uuid.UUID, ctx: Viewer) -> dict[str, Any]:
    a, b = await _get(ctx, other_id), await _get(ctx, plan_id)
    sa = {v["native_id"]: v for v in a.content["scope"]}
    sb = {v["native_id"]: v for v in b.content["scope"]}
    changed = [
        {"native_id": k, "from": sa[k].get("target_sku"), "to": sb[k].get("target_sku")}
        for k in sorted(sa.keys() & sb.keys())
        if sa[k].get("target_sku") != sb[k].get("target_sku")
    ]
    return {
        "from": {"id": str(a.id), "version": a.version, "hash": a.content_hash},
        "to": {"id": str(b.id), "version": b.version, "hash": b.content_hash},
        "added": sorted(sb.keys() - sa.keys()),
        "removed": sorted(sa.keys() - sb.keys()),
        "size_changes": changed,
        "totals": {"from": a.content["totals"], "to": b.content["totals"]},
    }


@router.get("/{plan_id}/export/{fmt}")
async def export(
    plan_id: uuid.UUID, fmt: Literal["json", "markdown", "opentofu"], ctx: Viewer, rid: RequestId
) -> Response:
    p = await _get(ctx, plan_id)
    slug = "".join(c if c.isalnum() else "-" for c in p.name.lower()).strip("-")[:50] or "plan"
    base = f"{slug}-v{p.version}"
    await _audit(ctx, "plan.export", p, rid, format=fmt)
    if fmt == "json":
        body = json.dumps(
            {
                "name": p.name,
                "version": p.version,
                "status": p.status,
                "content_hash": p.content_hash,
                "content": p.content,
            },
            indent=2,
            sort_keys=True,
        ).encode()
        return Response(
            body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base}.json"'},
        )
    if fmt == "markdown":
        md = to_markdown(p.name, p.version, p.content_hash, PlanContent.model_validate(p.content))
        return Response(
            md.encode(),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{base}.md"'},
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in sorted(p.iac.get("files", {}).items()):
            z.writestr(f"{base}/{name}", text)
        z.writestr(f"{base}/PLAN_HASH", p.content_hash + "\n")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{base}-opentofu.zip"'},
    )


@router.get("/{plan_id}/iac/{filename}")
async def iac_file(plan_id: uuid.UUID, filename: str, ctx: Viewer) -> Response:
    p = await _get(ctx, plan_id)
    files = p.iac.get("files", {})
    if filename not in files:
        raise NotFoundError("file not found")
    return Response(files[filename].encode(), media_type="text/plain; charset=utf-8")
