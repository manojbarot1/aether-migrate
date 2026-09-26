from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from aether.api.deps import WorkspaceContext, request_id, require_role
from aether.assessment.engine import RULES, RULESET_VERSION, Readiness, quota_needs
from aether.assessment.service import assess_vms
from aether.audit.writer import AuditRecord, record
from aether.catalog.store import load_region
from aether.core.enums import Role
from aether.core.errors import NotFoundError, ValidationFailedError
from aether.core.inventory import ResourceType
from aether.db.models import AssessmentRun, FindingAcknowledgement, Resource
from aether.sizing.engine import Strategy

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["assessment"])
Viewer = Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))]
Analyst = Annotated[WorkspaceContext, Depends(require_role(Role.ANALYST))]
RequestId = Annotated[str | None, Depends(request_id)]


class AssessRequest(BaseModel):
    resource_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    target_region: str
    strategy: Strategy = Strategy.LIKE_FOR_LIKE


class RunOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    target_provider: str
    target_region: str
    strategy: str
    ruleset_version: str
    summary: dict[str, Any]
    items: list[dict[str, Any]] | None = None


def _out(r: AssessmentRun, items: bool) -> RunOut:
    return RunOut(
        id=r.id,
        created_at=r.created_at,
        target_provider=r.target_provider,
        target_region=r.target_region,
        strategy=r.strategy,
        ruleset_version=r.ruleset_version,
        summary=r.summary,
        items=r.items if items else None,
    )


@router.get("/assessment-rules")
async def list_rules(_: Viewer) -> dict[str, Any]:
    return {
        "ruleset_version": RULESET_VERSION,
        "rules": [
            {"id": r.id, "version": r.version, "category": r.category, "doc": (r.fn.__doc__ or "").strip()}
            for r in RULES
        ],
    }


@router.post("/assessments", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def run_assessment(body: AssessRequest, ctx: Analyst, rid: RequestId) -> RunOut:
    vms = (
        (
            await ctx.session.execute(
                select(Resource).where(
                    Resource.id.in_(body.resource_ids), Resource.type == ResourceType.VM.value
                )
            )
        )
        .scalars()
        .all()
    )
    if not vms:
        raise NotFoundError("no virtual machines found for the given ids")
    target = await load_region(ctx.session, "azure", body.target_region)
    if not target.prices:
        raise ValidationFailedError(f"no Azure catalog for region '{body.target_region}'; run a catalog sync")
    results, sizings = await assess_vms(ctx.session, list(vms), target, body.strategy)
    counts = {r.value: sum(1 for x in results if x.readiness == r) for r in Readiness}
    by_rule: dict[str, int] = {}
    for x in results:
        for f in x.findings:
            if f.severity != "info" and not f.acknowledged:
                by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    size_by_id = dict(sizings)
    items = []
    for x in results:
        s = size_by_id.get(x.resource_id)
        top = s.candidates[0] if s and s.candidates else None
        items.append(
            {
                **x.model_dump(mode="json"),
                "target_sku": top.sku if top else None,
                "target_family": top.family if top else None,
            }
        )
    summary = {
        "vms": len(results),
        "readiness": counts,
        "average_score": round(sum(x.score for x in results) / len(results)),
        "top_issues": sorted(by_rule.items(), key=lambda kv: -kv[1])[:8],
        "quota_needs": quota_needs(sizings),
    }
    run = AssessmentRun(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        created_by=ctx.principal.user_id,
        target_provider="azure",
        target_region=body.target_region,
        strategy=body.strategy.value,
        ruleset_version=RULESET_VERSION,
        summary=summary,
        items=items,
    )
    ctx.session.add(run)
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="assessment.run",
            workspace_id=ctx.workspace_id,
            target_type="assessment",
            target_id=str(run.id),
            details={
                "target": f"azure/{body.target_region}",
                "vms": len(results),
                "readiness": counts,
                "ruleset": RULESET_VERSION,
            },
            request_id=rid,
        ),
    )
    await ctx.session.flush()
    await ctx.session.refresh(run)
    return _out(run, items=True)


@router.get("/assessments", response_model=list[RunOut])
async def list_assessments(ctx: Viewer, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[RunOut]:
    rows = (
        await ctx.session.execute(
            select(AssessmentRun)
            .where(AssessmentRun.workspace_id == ctx.workspace_id)
            .order_by(AssessmentRun.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [_out(r, items=False) for r in rows]


@router.get("/assessments/{run_id}", response_model=RunOut)
async def get_assessment(run_id: uuid.UUID, ctx: Viewer) -> RunOut:
    r = await ctx.session.get(AssessmentRun, run_id)
    if r is None or r.workspace_id != ctx.workspace_id:
        raise NotFoundError("assessment not found")
    return _out(r, items=True)


class AckRequest(BaseModel):
    native_id: str = Field(min_length=1, max_length=2048)
    rule_id: str = Field(pattern=r"^[A-Z]+-\d{3}$")
    reason: str = Field(min_length=5, max_length=2000)


@router.put("/acknowledgements", status_code=status.HTTP_204_NO_CONTENT)
async def acknowledge(body: AckRequest, ctx: Analyst, rid: RequestId) -> None:
    """Accept a reviewed warning. Blockers cannot be acknowledged; they must be resolved."""
    if not any(r.id == body.rule_id for r in RULES):
        raise ValidationFailedError("unknown rule")
    existing = await ctx.session.get(FindingAcknowledgement, (ctx.workspace_id, body.native_id, body.rule_id))
    if existing:
        existing.reason = body.reason
        existing.acknowledged_by = ctx.principal.user_id
        existing.acknowledged_by_display = ctx.principal.actor.display
    else:
        ctx.session.add(
            FindingAcknowledgement(
                workspace_id=ctx.workspace_id,
                native_id=body.native_id,
                rule_id=body.rule_id,
                reason=body.reason,
                acknowledged_by=ctx.principal.user_id,
                acknowledged_by_display=ctx.principal.actor.display,
            )
        )
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="assessment.acknowledge",
            workspace_id=ctx.workspace_id,
            target_type="finding",
            target_id=f"{body.native_id}:{body.rule_id}",
            details={"reason": body.reason},
            request_id=rid,
        ),
    )


@router.delete("/acknowledgements", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(ctx: Analyst, rid: RequestId, native_id: str, rule_id: str) -> None:
    existing = await ctx.session.get(FindingAcknowledgement, (ctx.workspace_id, native_id, rule_id))
    if existing is None:
        raise NotFoundError("acknowledgement not found")
    await ctx.session.delete(existing)
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="assessment.acknowledge.revoke",
            workspace_id=ctx.workspace_id,
            target_type="finding",
            target_id=f"{native_id}:{rule_id}",
            request_id=rid,
        ),
    )
