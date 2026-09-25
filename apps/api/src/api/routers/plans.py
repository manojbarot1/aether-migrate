"""Plans router — create, list, retrieve, approve, and export migration plans.

Endpoints:
  POST   /plans                      — create plan (analyst)
  GET    /plans                      — list plans for workspace (viewer)
  GET    /plans/{id}                 — full plan document (viewer)
  GET    /plans/{id}/diff/{other_id} — diff two plan versions (viewer)
  POST   /plans/{id}/approve         — approve plan (approver, not creator)
  POST   /plans/{id}/generate-narrative — generate AI narrative (analyst)
  GET    /plans/{id}/export/json     — download JSON (viewer)
  GET    /plans/{id}/export/markdown — download Markdown (viewer)
  GET    /plans/{id}/export/tofu     — download ZIP of OpenTofu module (analyst)
  POST   /plans/{id}/validate-tofu   — run tofu validate (analyst)

Immutability rule: approved plans cannot be modified.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])

_APPROVAL_VALID_DAYS = 30


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class PlanCreateRequest(BaseModel):
    resource_ids: list[uuid.UUID]
    name: str | None = None
    description: str | None = None
    target_provider: str
    target_region: str
    sizing_strategy: str = "right_sized"
    assumed_bandwidth_mbps: float | None = None
    dual_run_days: int = 14


class PlanApproveRequest(BaseModel):
    note: str | None = None


class PlanSummaryResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    target_provider: str
    target_region: str
    version: int
    content_hash: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    source_resource_count: int


class PlanDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    target_provider: str
    target_region: str
    version: int
    content_hash: str | None
    plan_document: dict[str, Any]
    ai_narrative: str | None
    ai_narrative_generated_at: datetime | None
    ai_narrative_model: str | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    approval_expires_at: datetime | None
    parent_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PlanDiffResponse(BaseModel):
    plan_a_id: str
    plan_b_id: str
    changed_fields: dict[str, Any]
    added_steps: list[int]
    removed_steps: list[int]
    changed_steps: list[int]


class ApproveResponse(BaseModel):
    plan_id: uuid.UUID
    status: str
    approved_by: uuid.UUID
    approved_at: datetime
    approval_expires_at: datetime
    plan_hash: str


class TofuValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_summary(row: Any) -> PlanSummaryResponse:
    source_ids = row.source_resource_ids or []
    return PlanSummaryResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        status=row.status,
        target_provider=row.target_provider,
        target_region=row.target_region,
        version=row.version,
        content_hash=row.content_hash,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        source_resource_count=len(source_ids),
    )


def _row_to_detail(row: Any) -> PlanDetailResponse:
    return PlanDetailResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        status=row.status,
        target_provider=row.target_provider,
        target_region=row.target_region,
        version=row.version,
        content_hash=row.content_hash,
        plan_document=row.plan_document or {},
        ai_narrative=row.ai_narrative,
        ai_narrative_generated_at=row.ai_narrative_generated_at,
        ai_narrative_model=row.ai_narrative_model,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        approval_expires_at=row.approval_expires_at,
        parent_id=row.parent_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _load_plan_or_404(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID,
    session: AsyncSession,
) -> Any:
    from db.models import PlanRow

    q = select(PlanRow).where(
        PlanRow.id == plan_id,
        PlanRow.workspace_id == workspace_id,
    )
    result = await session.execute(q)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return row


def _assert_not_approved(row: Any) -> None:
    if row.status == "approved":
        raise HTTPException(status_code=409, detail="Approved plans are immutable")


# ---------------------------------------------------------------------------
# POST /plans
# ---------------------------------------------------------------------------


@router.post("", response_model=PlanSummaryResponse, status_code=201)
async def create_plan(
    body: PlanCreateRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> PlanSummaryResponse:
    """Create a migration plan from the given resource IDs."""
    from audit.models import AuditEvent
    from audit.writer import AuditWriter
    from core.models import ProviderName
    from db.models import PlanRow, SnapshotRow
    from planner.engine import PlanEngine, PlanOptions
    from sizing.engine import SizingStrategy

    # Resolve target_provider
    try:
        target_provider = ProviderName(body.target_provider)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {body.target_provider}")

    try:
        sizing_strategy = SizingStrategy(body.sizing_strategy)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Unknown sizing_strategy: {body.sizing_strategy}"
        )

    # Find the latest completed snapshot for the workspace
    snap_q = (
        select(SnapshotRow)
        .where(
            SnapshotRow.workspace_id == workspace_id,
            SnapshotRow.status == "completed",
        )
        .order_by(SnapshotRow.completed_at.desc())
        .limit(1)
    )
    snap_result = await session.execute(snap_q)
    snapshot_row = snap_result.scalar_one_or_none()
    if snapshot_row is None:
        raise HTTPException(
            status_code=422,
            detail="No completed snapshot found for workspace — run discovery first",
        )

    options = PlanOptions(
        target_provider=target_provider,
        target_region=body.target_region,
        sizing_strategy=sizing_strategy,
        assumed_bandwidth_mbps=body.assumed_bandwidth_mbps,
        dual_run_days=body.dual_run_days,
    )

    new_id = uuid.uuid4()
    engine = PlanEngine()
    try:
        plan = await engine.create(
            resource_ids=body.resource_ids,
            options=options,
            snapshot_id=snapshot_row.id,
            db=session,
            plan_name=body.name,
            workspace_id=workspace_id,
            plan_id=new_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    plan_doc = plan.model_dump(mode="json")

    plan_row = PlanRow(
        id=new_id,
        workspace_id=workspace_id,
        name=plan.name,
        description=body.description,
        status="draft",
        source_resource_ids=[str(r) for r in body.resource_ids],
        target_provider=body.target_provider,
        target_region=body.target_region,
        sizing_strategy=body.sizing_strategy,
        snapshot_id=snapshot_row.id,
        catalog_version=plan.catalog_version,
        content_hash=plan.content_hash,
        plan_document=plan_doc,
        version=1,
        created_by=user.user_id,
    )
    session.add(plan_row)

    writer = AuditWriter(workspace_id=workspace_id)
    await writer.record(AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="plan.create",
        target_type="plan",
        target_id=str(new_id),
        arguments_redacted={
            "resource_count": len(body.resource_ids),
            "target": f"{body.target_provider}/{body.target_region}",
        },
        result_status="success",
    ))

    await session.commit()
    await session.refresh(plan_row)

    log.info(
        "plan_created",
        plan_id=str(new_id),
        hash=plan.content_hash[:8] if plan.content_hash else "n/a",
    )

    return _row_to_summary(plan_row)


# ---------------------------------------------------------------------------
# GET /plans
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PlanSummaryResponse])
async def list_plans(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> list[PlanSummaryResponse]:
    """List plans for the workspace, most recent first."""
    from db.models import PlanRow

    q = (
        select(PlanRow)
        .where(PlanRow.workspace_id == workspace_id)
        .order_by(PlanRow.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(q)
    return [_row_to_summary(r) for r in result.scalars().all()]


# ---------------------------------------------------------------------------
# GET /plans/{id}
# ---------------------------------------------------------------------------


@router.get("/{plan_id}", response_model=PlanDetailResponse)
async def get_plan(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> PlanDetailResponse:
    """Return the full plan document."""
    row = await _load_plan_or_404(plan_id, workspace_id, session)
    return _row_to_detail(row)


# ---------------------------------------------------------------------------
# GET /plans/{id}/diff/{other_id}
# ---------------------------------------------------------------------------


@router.get("/{plan_id}/diff/{other_id}", response_model=PlanDiffResponse)
async def diff_plan(
    plan_id: uuid.UUID,
    other_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> PlanDiffResponse:
    """Diff two plan versions."""
    from planner.diff import diff_plans
    from planner.models import MigrationPlan

    row_a = await _load_plan_or_404(plan_id, workspace_id, session)
    row_b = await _load_plan_or_404(other_id, workspace_id, session)

    plan_a = MigrationPlan.model_validate(row_a.plan_document)
    plan_b = MigrationPlan.model_validate(row_b.plan_document)

    diff = diff_plans(plan_a, plan_b)

    return PlanDiffResponse(
        plan_a_id=diff.plan_a_id,
        plan_b_id=diff.plan_b_id,
        changed_fields={k: list(v) for k, v in diff.changed_fields.items()},
        added_steps=diff.added_steps,
        removed_steps=diff.removed_steps,
        changed_steps=diff.changed_steps,
    )


# ---------------------------------------------------------------------------
# POST /plans/{id}/approve
# ---------------------------------------------------------------------------


@router.post("/{plan_id}/approve", response_model=ApproveResponse)
async def approve_plan(
    plan_id: uuid.UUID,
    body: PlanApproveRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("approver")),
) -> ApproveResponse:
    """Approve a plan.

    Enforces:
    - User must have 'approver' role
    - User must NOT be the plan creator (self-approval blocked)
    - Plan must not already be approved
    - plan.content_hash must be present
    """
    from audit.models import AuditEvent
    from audit.writer import AuditWriter
    from db.models import PlanApprovalRow

    row = await _load_plan_or_404(plan_id, workspace_id, session)

    if row.status == "approved":
        raise HTTPException(status_code=409, detail="Plan is already approved")

    if row.created_by == user.user_id:
        raise HTTPException(
            status_code=403, detail="Self-approval not permitted — a different user must approve"
        )

    if not row.content_hash:
        raise HTTPException(
            status_code=422, detail="Plan has no content_hash — recreate the plan"
        )

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=_APPROVAL_VALID_DAYS)

    approval_row = PlanApprovalRow(
        id=uuid.uuid4(),
        plan_id=plan_id,
        workspace_id=workspace_id,
        approver_id=user.user_id,
        plan_hash=row.content_hash,
        action="approved",
        note=body.note,
        expires_at=expires_at,
    )
    session.add(approval_row)

    # Update plan row
    row.status = "approved"
    row.approved_by = user.user_id
    row.approved_at = now
    row.approval_expires_at = expires_at

    writer = AuditWriter(workspace_id=workspace_id)
    await writer.record(AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="plan.approve",
        target_type="plan",
        target_id=str(plan_id),
        arguments_redacted={"plan_hash": row.content_hash[:8]},
        result_status="success",
    ))

    await session.commit()

    log.info("plan_approved", plan_id=str(plan_id), approver=user.email)

    return ApproveResponse(
        plan_id=plan_id,
        status="approved",
        approved_by=user.user_id,
        approved_at=now,
        approval_expires_at=expires_at,
        plan_hash=row.content_hash,
    )


# ---------------------------------------------------------------------------
# POST /plans/{id}/generate-narrative
# ---------------------------------------------------------------------------


@router.post("/{plan_id}/generate-narrative", response_model=PlanDetailResponse)
async def generate_narrative(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> PlanDetailResponse:
    """Generate an AI narrative summary for the plan.

    The narrative is stored separately from the plan document and is always
    labelled as AI-generated.
    """
    from audit.models import AuditEvent
    from audit.writer import AuditWriter

    row = await _load_plan_or_404(plan_id, workspace_id, session)
    _assert_not_approved(row)

    # Build a simple narrative from the plan document (no LLM in Phase 7)
    doc = row.plan_document or {}
    steps_count = len(doc.get("steps", []))
    prereqs_count = len(doc.get("prerequisites", []))
    findings_count = len(doc.get("assessment_findings", []))
    downtime = doc.get("downtime_estimate_minutes", 0)
    target = f"{row.target_provider}/{row.target_region}"

    narrative = (
        f"This migration plan covers {len(doc.get('source_resources', []))} virtual machine(s) "
        f"targeting {target}. "
        f"The plan consists of {steps_count} steps across all migration phases, "
        f"with {prereqs_count} prerequisites that must be completed before the migration window. "
        f"The assessment identified {findings_count} finding(s) for the source workloads. "
    )
    if downtime > 0:
        narrative += (
            f"Estimated downtime is approximately {downtime} minutes based on "
            f"{doc.get('downtime_basis', 'available data')}. "
        )
    else:
        narrative += (
            "Downtime estimate is unavailable — provide bandwidth information to calculate. "
        )
    narrative += (
        f"The plan includes a {doc.get('assumptions', [''])[0] if doc.get('assumptions') else 'standard'} "
        f"rollback procedure. Review all prerequisites and steps carefully before execution."
    )

    now = datetime.now(UTC)
    row.ai_narrative = narrative
    row.ai_narrative_generated_at = now
    row.ai_narrative_model = "aether-rule-based-v1"

    writer = AuditWriter(workspace_id=workspace_id)
    await writer.record(AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="plan.generate_narrative",
        target_type="plan",
        target_id=str(plan_id),
        arguments_redacted={},
        result_status="success",
    ))

    await session.commit()
    await session.refresh(row)

    return _row_to_detail(row)


# ---------------------------------------------------------------------------
# GET /plans/{id}/export/json
# ---------------------------------------------------------------------------


@router.get("/{plan_id}/export/json")
async def export_json(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> Response:
    """Download the plan as a schema-versioned JSON file."""
    from planner.export import export_json as _export_json
    from planner.models import MigrationPlan

    row = await _load_plan_or_404(plan_id, workspace_id, session)
    plan = MigrationPlan.model_validate(row.plan_document)
    content = _export_json(plan)

    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="plan-{plan_id}.json"'
        },
    )


# ---------------------------------------------------------------------------
# GET /plans/{id}/export/markdown
# ---------------------------------------------------------------------------


@router.get("/{plan_id}/export/markdown")
async def export_markdown(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> Response:
    """Download the plan as a Markdown document."""
    from planner.export import export_markdown as _export_markdown
    from planner.models import MigrationPlan

    row = await _load_plan_or_404(plan_id, workspace_id, session)
    plan = MigrationPlan.model_validate(row.plan_document)
    content = _export_markdown(plan)

    return Response(
        content=content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="plan-{plan_id}.md"'
        },
    )


# ---------------------------------------------------------------------------
# GET /plans/{id}/export/tofu
# ---------------------------------------------------------------------------


@router.get("/{plan_id}/export/tofu")
async def export_tofu(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> Response:
    """Download the OpenTofu module as a ZIP archive."""
    from iac.generators.azure import AzureTofuGenerator
    from planner.export import export_tofu_zip
    from planner.models import MigrationPlan

    row = await _load_plan_or_404(plan_id, workspace_id, session)
    plan = MigrationPlan.model_validate(row.plan_document)

    generator = AzureTofuGenerator()
    tofu_module = generator.generate(plan)
    content = export_tofu_zip(plan, tofu_module)

    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="plan-{plan_id}-tofu.zip"'
        },
    )


# ---------------------------------------------------------------------------
# POST /plans/{id}/validate-tofu
# ---------------------------------------------------------------------------


@router.post("/{plan_id}/validate-tofu", response_model=TofuValidationResponse)
async def validate_tofu(
    plan_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> TofuValidationResponse:
    """Run tofu validate on the generated module for this plan."""
    from iac.generators.azure import AzureTofuGenerator
    from iac.validator import validate_tofu_module
    from planner.models import MigrationPlan

    row = await _load_plan_or_404(plan_id, workspace_id, session)
    plan = MigrationPlan.model_validate(row.plan_document)

    generator = AzureTofuGenerator()
    tofu_module = generator.generate(plan)
    result = await validate_tofu_module(tofu_module)

    return TofuValidationResponse(
        valid=result.valid,
        errors=result.errors,
        warnings=result.warnings,
    )
