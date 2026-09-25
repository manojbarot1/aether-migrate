"""plan.create, plan.get, plan.explain — Phase 7 implementations."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# plan.create schemas
# ---------------------------------------------------------------------------


class PlanCreateInput(BaseModel):
    resource_ids: list[str]  # UUID strings
    name: str | None = None
    target_provider: str
    target_region: str
    sizing_strategy: str = "right_sized"
    assumed_bandwidth_mbps: float | None = None
    dual_run_days: int = 14


class PlanCreateOutput(BaseModel):
    plan_id: str
    name: str
    status: str
    content_hash: str | None
    version: int
    step_count: int
    downtime_estimate_minutes: int


# ---------------------------------------------------------------------------
# plan.get schemas
# ---------------------------------------------------------------------------


class PlanGetInput(BaseModel):
    plan_id: str


class PlanGetOutput(BaseModel):
    plan_id: str
    name: str
    status: str
    version: int
    content_hash: str | None
    target_provider: str
    target_region: str
    prerequisites: list[str]
    steps: list[dict[str, Any]]
    downtime_estimate_minutes: int
    downtime_basis: str
    assumptions: list[str]


# ---------------------------------------------------------------------------
# plan.explain schemas
# ---------------------------------------------------------------------------


class PlanExplainInput(BaseModel):
    plan_id: str


class PlanExplainOutput(BaseModel):
    plan_id: str
    explanation: str
    is_ai_generated: bool
    generated_at: str | None


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

PLAN_CREATE_TOOL = ToolDefinition(
    name="plan.create",
    description=(
        "Create a deterministic migration plan for a list of VM resource IDs. "
        "Runs sizing and assessment, generates ordered steps, and computes a content hash. "
        "Returns the plan summary including the hash. Requires analyst role."
    ),
    input_schema=PlanCreateInput,
    output_schema=PlanCreateOutput,
    side_effect_class="draft",
    required_role="analyst",
    tags=["plan"],
)

PLAN_GET_TOOL = ToolDefinition(
    name="plan.get",
    description=(
        "Retrieve an existing migration plan by ID. "
        "Returns the full plan document including all steps, prerequisites, and estimates."
    ),
    input_schema=PlanGetInput,
    output_schema=PlanGetOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["plan"],
)

PLAN_EXPLAIN_TOOL = ToolDefinition(
    name="plan.explain",
    description=(
        "Return the narrative explanation of a migration plan. "
        "If no AI narrative has been generated yet, returns a rule-based summary. "
        "Always labelled as AI-generated when from the AI narrative field."
    ),
    input_schema=PlanExplainInput,
    output_schema=PlanExplainOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["plan"],
)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def plan_create_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    """Create a migration plan and persist it to the DB."""
    from core.models import ProviderName
    from db.models import PlanRow, SnapshotRow
    from planner.engine import PlanEngine, PlanOptions
    from sizing.engine import SizingStrategy
    from sqlalchemy import select

    inp = PlanCreateInput.model_validate(input_data)

    workspace_id = uuid.UUID(user.workspace_id)
    resource_ids = [uuid.UUID(r) for r in inp.resource_ids]

    target_provider = ProviderName(inp.target_provider)
    sizing_strategy = SizingStrategy(inp.sizing_strategy)

    # Find latest snapshot
    snap_q = (
        select(SnapshotRow)
        .where(
            SnapshotRow.workspace_id == workspace_id,
            SnapshotRow.status == "completed",
        )
        .order_by(SnapshotRow.completed_at.desc())
        .limit(1)
    )
    snap_result = await db.execute(snap_q)
    snapshot_row = snap_result.scalar_one_or_none()
    if snapshot_row is None:
        raise ValueError("No completed snapshot found — run discovery first")

    options = PlanOptions(
        target_provider=target_provider,
        target_region=inp.target_region,
        sizing_strategy=sizing_strategy,
        assumed_bandwidth_mbps=inp.assumed_bandwidth_mbps,
        dual_run_days=inp.dual_run_days,
    )

    new_id = uuid.uuid4()
    engine = PlanEngine()
    plan = await engine.create(
        resource_ids=resource_ids,
        options=options,
        snapshot_id=snapshot_row.id,
        db=db,
        plan_name=inp.name,
        workspace_id=workspace_id,
        plan_id=new_id,
    )

    plan_row = PlanRow(
        id=new_id,
        workspace_id=workspace_id,
        name=plan.name,
        status="draft",
        source_resource_ids=[str(r) for r in resource_ids],
        target_provider=inp.target_provider,
        target_region=inp.target_region,
        sizing_strategy=inp.sizing_strategy,
        snapshot_id=snapshot_row.id,
        catalog_version=plan.catalog_version,
        content_hash=plan.content_hash,
        plan_document=plan.model_dump(mode="json"),
        version=1,
        created_by=uuid.UUID(user.user_id),
    )
    db.add(plan_row)
    await db.commit()

    return PlanCreateOutput(
        plan_id=str(new_id),
        name=plan.name,
        status="draft",
        content_hash=plan.content_hash,
        version=1,
        step_count=len(plan.steps),
        downtime_estimate_minutes=plan.downtime_estimate_minutes,
    ).model_dump()


async def plan_get_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    """Load a plan from the DB and return the document."""
    from db.models import PlanRow
    from planner.models import MigrationPlan
    from sqlalchemy import select

    inp = PlanGetInput.model_validate(input_data)
    workspace_id = uuid.UUID(user.workspace_id)

    q = select(PlanRow).where(
        PlanRow.id == uuid.UUID(inp.plan_id),
        PlanRow.workspace_id == workspace_id,
    )
    result = await db.execute(q)
    row = result.scalar_one_or_none()
    if row is None:
        raise KeyError(f"Plan {inp.plan_id} not found")

    plan = MigrationPlan.model_validate(row.plan_document)

    return PlanGetOutput(
        plan_id=inp.plan_id,
        name=plan.name,
        status=row.status,
        version=plan.version,
        content_hash=plan.content_hash,
        target_provider=plan.target_provider.value,
        target_region=plan.target_region,
        prerequisites=plan.prerequisites,
        steps=[s.model_dump() for s in plan.steps],
        downtime_estimate_minutes=plan.downtime_estimate_minutes,
        downtime_basis=plan.downtime_basis,
        assumptions=plan.assumptions,
    ).model_dump()


async def plan_explain_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    """Return the narrative explanation for a plan."""
    from db.models import PlanRow
    from planner.models import MigrationPlan
    from sqlalchemy import select

    inp = PlanExplainInput.model_validate(input_data)
    workspace_id = uuid.UUID(user.workspace_id)

    q = select(PlanRow).where(
        PlanRow.id == uuid.UUID(inp.plan_id),
        PlanRow.workspace_id == workspace_id,
    )
    result = await db.execute(q)
    row = result.scalar_one_or_none()
    if row is None:
        raise KeyError(f"Plan {inp.plan_id} not found")

    if row.ai_narrative:
        return PlanExplainOutput(
            plan_id=inp.plan_id,
            explanation=row.ai_narrative,
            is_ai_generated=True,
            generated_at=(
                row.ai_narrative_generated_at.isoformat()
                if row.ai_narrative_generated_at
                else None
            ),
        ).model_dump()

    # Build a placeholder summary from the plan document
    plan = MigrationPlan.model_validate(row.plan_document)
    explanation = (
        f"Migration plan '{plan.name}' (v{plan.version}) targets "
        f"{plan.target_provider.value}/{plan.target_region}. "
        f"It has {len(plan.steps)} steps, {len(plan.prerequisites)} prerequisites, "
        f"and an estimated downtime of {plan.downtime_estimate_minutes} minutes. "
        f"No AI narrative has been generated yet — use the generate-narrative endpoint "
        f"to create a detailed explanation."
    )

    return PlanExplainOutput(
        plan_id=inp.plan_id,
        explanation=explanation,
        is_ai_generated=False,
        generated_at=None,
    ).model_dump()
