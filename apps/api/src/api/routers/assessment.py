"""Assessment router — run readiness assessments and manage finding acknowledgements.

All endpoints are workspace-scoped. Analyst role required for run/acknowledge.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/assessment", tags=["assessment"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AssessmentRunRequest(BaseModel):
    resource_id: uuid.UUID
    target_provider: str
    target_region: str


class AcknowledgeRequest(BaseModel):
    reason: str
    expires_at: datetime | None = None


class FindingRecordResponse(BaseModel):
    rule_id: str
    rule_version: str
    severity: str
    applies_to: str
    title: str
    message: str
    evidence: dict[str, Any]
    remediation: str
    docs_url: str | None
    acknowledged: bool
    acknowledged_reason: str | None


class ReadinessScoreResponse(BaseModel):
    status: str
    score: int
    blockers: int
    warnings: int
    info: int


class AssessmentResultResponse(BaseModel):
    resource_id: str
    target_provider: str
    target_region: str
    readiness: ReadinessScoreResponse
    findings: list[FindingRecordResponse]
    snapshot_id: str
    snapshot_time: datetime
    catalog_version: str


class AssessmentHistoryItem(BaseModel):
    id: uuid.UUID
    target_provider: str
    target_region: str
    readiness: str
    readiness_score: int
    blocker_count: int
    warning_count: int
    info_count: int
    created_at: datetime
    completed_at: datetime | None


class AcknowledgeResponse(BaseModel):
    resource_id: str
    rule_id: str
    acknowledged_by: str
    reason: str
    expires_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_result_response(result: Any) -> AssessmentResultResponse:
    """Convert an AssessmentResult domain object to the API response schema."""
    from assessment.models import AssessmentResult

    if not isinstance(result, AssessmentResult):
        raise TypeError(f"Expected AssessmentResult, got {type(result)}")

    return AssessmentResultResponse(
        resource_id=result.resource_id,
        target_provider=result.target_provider.value,
        target_region=result.target_region,
        readiness=ReadinessScoreResponse(
            status=result.readiness.status,
            score=result.readiness.score,
            blockers=result.readiness.blockers,
            warnings=result.readiness.warnings,
            info=result.readiness.info,
        ),
        findings=[
            FindingRecordResponse(
                rule_id=f.rule_id,
                rule_version=f.rule_version,
                severity=f.severity.value,
                applies_to=f.applies_to,
                title=f.title,
                message=f.message,
                evidence=f.evidence,
                remediation=f.remediation,
                docs_url=f.docs_url,
                acknowledged=f.acknowledged,
                acknowledged_reason=f.acknowledged_reason,
            )
            for f in result.findings
        ],
        snapshot_id=result.snapshot_id,
        snapshot_time=result.snapshot_time,
        catalog_version=result.catalog_version,
    )


# ---------------------------------------------------------------------------
# POST /assessment/run
# ---------------------------------------------------------------------------


@router.post("/run", response_model=AssessmentResultResponse)
async def run_assessment(
    body: AssessmentRunRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> AssessmentResultResponse:
    """Run a migration readiness assessment for a resource.

    Loads the resource's VMSpec from the database, runs all rules, and
    persists the result. Returns the full AssessmentResult.
    """
    from assessment.engine import AssessmentEngine
    from assessment.models import AssessmentResult
    from assessment.registry import RuleRegistry
    from audit.models import AuditEvent
    from audit.writer import AuditWriter
    from core.models import EdgeKind, ProviderName, ResourceEdge, VMSpec
    from db.models import (
        AssessmentResultRow,
        FindingAcknowledgementRow,
        ResourceEdgeRow,
        ResourceRow,
    )

    # --- Load resource ---
    resource_result = await session.execute(
        select(ResourceRow).where(
            ResourceRow.id == body.resource_id,
            ResourceRow.workspace_id == workspace_id,
            ResourceRow.kind == "vm",
        )
    )
    resource_row = resource_result.scalar_one_or_none()
    if resource_row is None:
        raise HTTPException(status_code=404, detail="VM resource not found")

    # --- Reconstruct VMSpec from DB row ---
    try:
        provider = ProviderName(resource_row.provider)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {resource_row.provider}")

    try:
        target_provider = ProviderName(body.target_provider)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown target_provider: {body.target_provider}")

    spec = resource_row.spec or {}
    vm = VMSpec(
        id=resource_row.id,
        workspace_id=resource_row.workspace_id,
        connection_id=resource_row.connection_id,
        provider=provider,
        native_id=resource_row.native_id,
        account=resource_row.account,
        region=resource_row.region,
        zone=resource_row.zone,
        name=resource_row.name,
        snapshot_id=resource_row.snapshot_id,
        tags=resource_row.tags or {},
        os_name=spec.get("os_name"),
        os_version=spec.get("os_version"),
        vcpu=spec.get("vcpu"),
        memory_gib=spec.get("memory_gib"),
        architecture=spec.get("architecture"),
        instance_type=spec.get("instance_type"),
        extra=spec.get("extra", {}),
    )
    # Reconstruct disks from spec
    from core.models import DiskSpec, NicSpec

    for d in spec.get("disks", []):
        vm.disks.append(DiskSpec(
            size_gib=d.get("size_gib"),
            type_class=d.get("type_class"),
            iops=d.get("iops"),
            throughput_mbps=d.get("throughput_mbps"),
            boot=d.get("boot", False),
            encrypted=d.get("encrypted", False),
            ephemeral=d.get("ephemeral", False),
        ))
    for n in spec.get("nics", []):
        vm.nics.append(NicSpec(
            private_ips=n.get("private_ips", []),
            public_ips=n.get("public_ips", []),
            security_group_ids=n.get("security_group_ids", []),
        ))

    # --- Load topology edges ---
    edges_result = await session.execute(
        select(ResourceEdgeRow).where(
            ResourceEdgeRow.from_id == body.resource_id,
            ResourceEdgeRow.workspace_id == workspace_id,
        )
    )
    edges = [
        ResourceEdge(
            from_id=e.from_id,
            to_id=e.to_id,
            kind=EdgeKind(e.kind),
            workspace_id=e.workspace_id,
            snapshot_id=e.snapshot_id,
        )
        for e in edges_result.scalars().all()
        if e.kind in [k.value for k in EdgeKind]
    ]

    # --- Load active acknowledgements ---
    ack_result = await session.execute(
        select(FindingAcknowledgementRow).where(
            FindingAcknowledgementRow.workspace_id == workspace_id,
            FindingAcknowledgementRow.resource_id == body.resource_id,
        )
    )
    acknowledgements = list(ack_result.scalars().all())

    # --- Run assessment ---
    registry = RuleRegistry.build()
    engine = AssessmentEngine(rules=registry.get_all())
    result: AssessmentResult = await engine.run(
        source_vm=vm,
        target_provider=target_provider,
        target_region=body.target_region,
        edges=edges,
        db=session,
        acknowledgements=acknowledgements,
    )

    # --- Persist result ---

    result_row = AssessmentResultRow(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        resource_id=body.resource_id,
        snapshot_id=resource_row.snapshot_id,
        target_provider=body.target_provider,
        target_region=body.target_region,
        status="completed",
        readiness=result.readiness.status,
        readiness_score=result.readiness.score,
        blocker_count=result.readiness.blockers,
        warning_count=result.readiness.warnings,
        info_count=result.readiness.info,
        findings=[f.model_dump() for f in result.findings],
        completed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(result_row)

    # --- Audit ---
    writer = AuditWriter(workspace_id=workspace_id)
    await writer.record(AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="assessment.run",
        target_type="resource",
        target_id=str(body.resource_id),
        arguments_redacted={
            "target_provider": body.target_provider,
            "target_region": body.target_region,
        },
        result_status="success",
    ))

    await session.commit()

    log.info(
        "assessment_run",
        resource_id=str(body.resource_id),
        target=f"{body.target_provider}/{body.target_region}",
        readiness=result.readiness.status,
        score=result.readiness.score,
    )

    return _to_result_response(result)


# ---------------------------------------------------------------------------
# GET /assessment/results/{resource_id}
# ---------------------------------------------------------------------------


@router.get("/results/{resource_id}", response_model=list[AssessmentHistoryItem])
async def list_assessment_results(
    resource_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> list[AssessmentHistoryItem]:
    """List past assessment results for a resource (latest per target provider)."""
    from db.models import AssessmentResultRow

    q = (
        select(AssessmentResultRow)
        .where(
            AssessmentResultRow.workspace_id == workspace_id,
            AssessmentResultRow.resource_id == resource_id,
        )
        .order_by(AssessmentResultRow.created_at.desc())
        .limit(50)
    )
    result = await session.execute(q)
    rows = result.scalars().all()

    # Deduplicate — latest per (target_provider, target_region)
    seen: set[tuple[str, str]] = set()
    items: list[AssessmentHistoryItem] = []
    for row in rows:
        key = (row.target_provider, row.target_region)
        if key not in seen:
            seen.add(key)
            items.append(AssessmentHistoryItem(
                id=row.id,
                target_provider=row.target_provider,
                target_region=row.target_region,
                readiness=row.readiness,
                readiness_score=row.readiness_score,
                blocker_count=row.blocker_count,
                warning_count=row.warning_count,
                info_count=row.info_count,
                created_at=row.created_at,
                completed_at=row.completed_at,
            ))

    return items


# ---------------------------------------------------------------------------
# POST /assessment/findings/{resource_id}/{rule_id}/acknowledge
# ---------------------------------------------------------------------------


@router.post(
    "/findings/{resource_id}/{rule_id}/acknowledge",
    response_model=AcknowledgeResponse,
)
async def acknowledge_finding(
    resource_id: uuid.UUID,
    rule_id: str,
    body: AcknowledgeRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> AcknowledgeResponse:
    """Acknowledge a finding, suppressing it from future readiness scores.

    Creates or replaces the acknowledgement for the given resource+rule.
    """
    from audit.models import AuditEvent
    from audit.writer import AuditWriter
    from db.models import FindingAcknowledgementRow, ResourceRow

    # Verify resource exists in workspace
    resource_result = await session.execute(
        select(ResourceRow).where(
            ResourceRow.id == resource_id,
            ResourceRow.workspace_id == workspace_id,
        )
    )
    if resource_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    # Upsert acknowledgement
    existing_result = await session.execute(
        select(FindingAcknowledgementRow).where(
            FindingAcknowledgementRow.workspace_id == workspace_id,
            FindingAcknowledgementRow.resource_id == resource_id,
            FindingAcknowledgementRow.rule_id == rule_id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
        existing.reason = body.reason
        existing.expires_at = body.expires_at
        existing.acknowledged_by = uuid.UUID(str(user.user_id))
    else:
        session.add(FindingAcknowledgementRow(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            resource_id=resource_id,
            rule_id=rule_id,
            acknowledged_by=uuid.UUID(str(user.user_id)),
            reason=body.reason,
            expires_at=body.expires_at,
        ))

    # Audit
    writer = AuditWriter(workspace_id=workspace_id)
    await writer.record(AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="assessment.acknowledge",
        target_type="finding",
        target_id=f"{resource_id}/{rule_id}",
        arguments_redacted={"rule_id": rule_id},
        result_status="success",
    ))

    await session.commit()

    log.info(
        "finding_acknowledged",
        resource_id=str(resource_id),
        rule_id=rule_id,
        user=user.email,
    )

    return AcknowledgeResponse(
        resource_id=str(resource_id),
        rule_id=rule_id,
        acknowledged_by=user.email,
        reason=body.reason,
        expires_at=body.expires_at,
    )


# ---------------------------------------------------------------------------
# DELETE /assessment/findings/{resource_id}/{rule_id}/acknowledge
# ---------------------------------------------------------------------------


@router.delete(
    "/findings/{resource_id}/{rule_id}/acknowledge",
    status_code=204,
)
async def delete_acknowledgement(
    resource_id: uuid.UUID,
    rule_id: str,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> None:
    """Remove a finding acknowledgement."""
    from audit.models import AuditEvent
    from audit.writer import AuditWriter
    from db.models import FindingAcknowledgementRow

    result = await session.execute(
        select(FindingAcknowledgementRow).where(
            FindingAcknowledgementRow.workspace_id == workspace_id,
            FindingAcknowledgementRow.resource_id == resource_id,
            FindingAcknowledgementRow.rule_id == rule_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Acknowledgement not found")

    await session.delete(row)

    # Audit
    writer = AuditWriter(workspace_id=workspace_id)
    await writer.record(AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="assessment.acknowledge.delete",
        target_type="finding",
        target_id=f"{resource_id}/{rule_id}",
        arguments_redacted={"rule_id": rule_id},
        result_status="success",
    ))

    await session.commit()

    log.info(
        "acknowledgement_deleted",
        resource_id=str(resource_id),
        rule_id=rule_id,
        user=user.email,
    )
