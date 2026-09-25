"""assessment.run — AI tool for running migration readiness assessments."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AssessmentRunInput(BaseModel):
    resource_id: str
    target_provider: str
    target_region: str


class ReadinessScoreOutput(BaseModel):
    status: str
    score: int
    blockers: int
    warnings: int
    info: int


class FindingOutput(BaseModel):
    rule_id: str
    severity: str
    title: str
    message: str
    remediation: str
    acknowledged: bool


class AssessmentToolOutput(BaseModel):
    resource_id: str
    target_provider: str
    target_region: str
    readiness: ReadinessScoreOutput
    findings: list[FindingOutput]
    snapshot_id: str
    catalog_version: str


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

ASSESSMENT_RUN_TOOL = ToolDefinition(
    name="assessment.run",
    description=(
        "Run a migration readiness assessment for a specific VM resource against a "
        "target cloud provider and region. Returns a readiness score (0-100), status "
        "(ready / ready_with_warnings / blocked), and a list of findings with "
        "remediation guidance. "
        "Input: resource_id (UUID), target_provider (aws/azure/gcp/ibm), target_region."
    ),
    input_schema=AssessmentRunInput,
    output_schema=AssessmentToolOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["assessment"],
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def assessment_run_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    """Run the assessment engine and return a serialized AssessmentToolOutput."""
    import uuid

    from assessment.engine import AssessmentEngine
    from assessment.registry import RuleRegistry
    from core.models import DiskSpec, EdgeKind, NicSpec, ProviderName, ResourceEdge, VMSpec
    from db.models import FindingAcknowledgementRow, ResourceEdgeRow, ResourceRow
    from sqlalchemy import select

    parsed = AssessmentRunInput(**input_data)
    resource_uuid = uuid.UUID(parsed.resource_id)
    workspace_uuid = uuid.UUID(user.workspace_id)

    try:
        target_provider = ProviderName(parsed.target_provider)
    except ValueError:
        raise ValueError(f"Unknown target_provider: {parsed.target_provider!r}")

    # --- Load resource ---
    result = await db.execute(
        select(ResourceRow).where(
            ResourceRow.id == resource_uuid,
            ResourceRow.workspace_id == workspace_uuid,
            ResourceRow.kind == "vm",
        )
    )
    resource_row = result.scalar_one_or_none()
    if resource_row is None:
        raise ValueError(f"VM resource {parsed.resource_id} not found in workspace")

    provider = ProviderName(resource_row.provider)
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
    edges_result = await db.execute(
        select(ResourceEdgeRow).where(
            ResourceEdgeRow.from_id == resource_uuid,
            ResourceEdgeRow.workspace_id == workspace_uuid,
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

    # --- Load acknowledgements ---
    ack_result = await db.execute(
        select(FindingAcknowledgementRow).where(
            FindingAcknowledgementRow.workspace_id == workspace_uuid,
            FindingAcknowledgementRow.resource_id == resource_uuid,
        )
    )
    acknowledgements = list(ack_result.scalars().all())

    # --- Run engine ---
    registry = RuleRegistry.build()
    engine = AssessmentEngine(rules=registry.get_all())
    assessment_result = await engine.run(
        source_vm=vm,
        target_provider=target_provider,
        target_region=parsed.target_region,
        edges=edges,
        db=db,
        acknowledgements=acknowledgements,
    )

    output = AssessmentToolOutput(
        resource_id=assessment_result.resource_id,
        target_provider=assessment_result.target_provider.value,
        target_region=assessment_result.target_region,
        readiness=ReadinessScoreOutput(
            status=assessment_result.readiness.status,
            score=assessment_result.readiness.score,
            blockers=assessment_result.readiness.blockers,
            warnings=assessment_result.readiness.warnings,
            info=assessment_result.readiness.info,
        ),
        findings=[
            FindingOutput(
                rule_id=f.rule_id,
                severity=f.severity.value,
                title=f.title,
                message=f.message,
                remediation=f.remediation,
                acknowledged=f.acknowledged,
            )
            for f in assessment_result.findings
        ],
        snapshot_id=assessment_result.snapshot_id,
        catalog_version=assessment_result.catalog_version,
    )
    return output.model_dump()
