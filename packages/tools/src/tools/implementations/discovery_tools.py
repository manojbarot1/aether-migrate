"""discovery.status and discovery.refresh tool implementations."""

from __future__ import annotations

import uuid
from typing import Any

from db.models import ConnectionRow, ResourceRow, SnapshotRow
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# discovery.status schemas
# ---------------------------------------------------------------------------


class DiscoveryStatusInput(BaseModel):
    workspace_id: str
    connection_id: str | None = None


class SnapshotSummary(BaseModel):
    connection_id: str
    snapshot_id: str
    status: str
    completed_at: str | None
    coverage_summary: dict[str, str]
    vm_count: int


class DiscoveryStatusOutput(BaseModel):
    snapshots: list[SnapshotSummary]


# ---------------------------------------------------------------------------
# discovery.refresh schemas
# ---------------------------------------------------------------------------


class DiscoveryRefreshInput(BaseModel):
    connection_id: str


class DiscoveryRefreshOutput(BaseModel):
    job_id: str
    snapshot_id: str
    message: str


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

DISCOVERY_STATUS_TOOL = ToolDefinition(
    name="discovery.status",
    description=(
        "Return the latest discovery snapshot status for each connection in "
        "the workspace. Optionally filter to a specific connection."
    ),
    input_schema=DiscoveryStatusInput,
    output_schema=DiscoveryStatusOutput,
    side_effect_class="read",
    required_role="viewer",
    tags=["discovery"],
)

DISCOVERY_REFRESH_TOOL = ToolDefinition(
    name="discovery.refresh",
    description=(
        "Trigger a new discovery run for the specified connection. "
        "Returns a job ID and a new snapshot ID that will be populated as "
        "the run completes."
    ),
    input_schema=DiscoveryRefreshInput,
    output_schema=DiscoveryRefreshOutput,
    side_effect_class="read-workflow",
    required_role="connection-admin",
    tags=["discovery"],
)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def discovery_status_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    query = select(SnapshotRow).where(
        SnapshotRow.workspace_id == user.workspace_id  # type: ignore[arg-type]
    )
    inp = DiscoveryStatusInput.model_validate(input_data)
    if inp.connection_id:
        query = query.where(SnapshotRow.connection_id == inp.connection_id)  # type: ignore[arg-type]

    # Get the latest snapshot per connection by ordering descending and
    # using a subquery approach (we load all and keep the latest per conn).
    result = await db.execute(query.order_by(SnapshotRow.started_at.desc()))
    all_snapshots = result.scalars().all()

    # Keep only the most recent snapshot per connection
    seen: set[str] = set()
    latest: list[SnapshotRow] = []
    for snap in all_snapshots:
        key = str(snap.connection_id)
        if key not in seen:
            seen.add(key)
            latest.append(snap)

    # Count VMs per snapshot
    summaries: list[SnapshotSummary] = []
    for snap in latest:
        count_result = await db.execute(
            select(func.count()).select_from(ResourceRow).where(
                ResourceRow.snapshot_id == snap.id,  # type: ignore[arg-type]
                ResourceRow.kind == "vm",
            )
        )
        vm_count = count_result.scalar_one()
        summaries.append(
            SnapshotSummary(
                connection_id=str(snap.connection_id),
                snapshot_id=str(snap.id),
                status=snap.status,
                completed_at=(
                    snap.completed_at.isoformat() if snap.completed_at else None
                ),
                coverage_summary=snap.coverage or {},
                vm_count=vm_count,
            )
        )

    return DiscoveryStatusOutput(snapshots=summaries).model_dump()


async def discovery_refresh_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    inp = DiscoveryRefreshInput.model_validate(input_data)

    # Verify the connection exists and belongs to this workspace
    conn_result = await db.execute(
        select(ConnectionRow).where(
            ConnectionRow.id == inp.connection_id,  # type: ignore[arg-type]
            ConnectionRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
        )
    )
    connection = conn_result.scalar_one_or_none()
    if connection is None:
        raise KeyError(f"Connection '{inp.connection_id}' not found")

    # Create a new snapshot row in "running" state
    snapshot_id = uuid.uuid4()
    new_snapshot = SnapshotRow(
        id=snapshot_id,
        workspace_id=user.workspace_id,  # type: ignore[arg-type]
        connection_id=connection.id,
        provider=connection.provider,
        status="running",
        coverage={},
    )
    db.add(new_snapshot)
    await db.flush()

    # In a full implementation this would enqueue a Temporal workflow.
    # For Phase 3 we return the snapshot ID so the worker can pick it up.
    job_id = str(uuid.uuid4())

    return DiscoveryRefreshOutput(
        job_id=job_id,
        snapshot_id=str(snapshot_id),
        message=(
            f"Discovery started for connection '{connection.name}'. "
            f"Snapshot {snapshot_id} will be updated as resources are ingested."
        ),
    ).model_dump()
