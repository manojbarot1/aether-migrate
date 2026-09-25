"""Inventory tool implementations.

Tools:
    inventory.search_vms    — query VMs with filters
    inventory.get_resource  — full resource detail
    inventory.diff_snapshots — diff two snapshots
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from db.models import ResourceRow, SnapshotRow
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# inventory.search_vms schemas
# ---------------------------------------------------------------------------


class SearchVMsInput(BaseModel):
    provider: str | None = None
    region: str | None = None
    min_vcpu: int | None = None
    min_memory_gib: float | None = None
    status: str | None = None
    limit: int = 20
    snapshot_id: str | None = None


class VMSummary(BaseModel):
    id: str
    name: str | None
    provider: str
    region: str
    vcpu: int | None
    memory_gib: float | None
    os_family: str | None
    status: str
    source_sku: str | None
    snapshot_time: str | None


class SearchVMsOutput(BaseModel):
    vms: list[VMSummary]
    total: int
    snapshot_time: datetime | None
    coverage_warning: str | None


# ---------------------------------------------------------------------------
# inventory.get_resource schemas
# ---------------------------------------------------------------------------


class GetResourceInput(BaseModel):
    resource_id: str


class GetResourceOutput(BaseModel):
    resource: dict[str, Any]


# ---------------------------------------------------------------------------
# inventory.diff_snapshots schemas
# ---------------------------------------------------------------------------


class DiffSnapshotsInput(BaseModel):
    snapshot_id_a: str
    snapshot_id_b: str


class DiffSnapshotsOutput(BaseModel):
    added: list[str]
    removed: list[str]
    changed: list[str]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

INVENTORY_SEARCH_VMS_TOOL = ToolDefinition(
    name="inventory.search_vms",
    description=(
        "Search for virtual machines in the inventory with optional filters "
        "for provider, region, vCPU count, memory, and lifecycle status. "
        "Results come from the latest discovery snapshot."
    ),
    input_schema=SearchVMsInput,
    output_schema=SearchVMsOutput,
    side_effect_class="read",
    required_role="viewer",
    tags=["inventory", "vm"],
)

INVENTORY_GET_RESOURCE_TOOL = ToolDefinition(
    name="inventory.get_resource",
    description=(
        "Return the full specification for a single resource by its internal ID. "
        "Includes all normalized fields and provider-specific extras."
    ),
    input_schema=GetResourceInput,
    output_schema=GetResourceOutput,
    side_effect_class="read",
    required_role="viewer",
    tags=["inventory"],
)

INVENTORY_DIFF_SNAPSHOTS_TOOL = ToolDefinition(
    name="inventory.diff_snapshots",
    description=(
        "Compare two discovery snapshots and return the sets of resource IDs "
        "that were added, removed, or changed between them."
    ),
    input_schema=DiffSnapshotsInput,
    output_schema=DiffSnapshotsOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["inventory", "diff"],
)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _extract_vm_summary(row: ResourceRow, snapshot_time: str | None) -> VMSummary:
    spec = row.spec or {}
    return VMSummary(
        id=str(row.id),
        name=row.name,
        provider=row.provider,
        region=row.region,
        vcpu=spec.get("vcpu"),
        memory_gib=spec.get("memory_gib"),
        os_family=spec.get("os_name"),
        status=row.status,
        source_sku=spec.get("instance_type"),
        snapshot_time=snapshot_time,
    )


async def inventory_search_vms_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    inp = SearchVMsInput.model_validate(input_data)

    query = select(ResourceRow).where(
        ResourceRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
        ResourceRow.kind == "vm",
    )

    if inp.provider:
        query = query.where(ResourceRow.provider == inp.provider)
    if inp.region:
        query = query.where(ResourceRow.region == inp.region)
    if inp.status:
        query = query.where(ResourceRow.status == inp.status)

    # If a specific snapshot is requested, scope to it
    if inp.snapshot_id:
        query = query.where(ResourceRow.snapshot_id == inp.snapshot_id)  # type: ignore[arg-type]

    result = await db.execute(query)
    rows = result.scalars().all()

    # Apply in-memory filters for JSONB spec fields
    filtered: list[ResourceRow] = []
    for row in rows:
        spec = row.spec or {}
        if inp.min_vcpu is not None:
            vcpu = spec.get("vcpu") or 0
            if vcpu < inp.min_vcpu:
                continue
        if inp.min_memory_gib is not None:
            mem = spec.get("memory_gib") or 0.0
            if mem < inp.min_memory_gib:
                continue
        filtered.append(row)

    total = len(filtered)
    page = filtered[: inp.limit]

    # Determine snapshot time from the first result
    snapshot_time_dt: datetime | None = None
    coverage_warning: str | None = None
    if page:
        snap_id = page[0].snapshot_id
        if snap_id:
            snap_result = await db.execute(
                select(SnapshotRow).where(SnapshotRow.id == snap_id)
            )
            snap = snap_result.scalar_one_or_none()
            if snap:
                snapshot_time_dt = snap.completed_at
                if snap.status != "completed":
                    coverage_warning = (
                        f"Snapshot {snap_id} has status '{snap.status}' — "
                        "results may be incomplete."
                    )

    snapshot_time_str = (
        snapshot_time_dt.isoformat() if snapshot_time_dt else None
    )
    vms = [_extract_vm_summary(r, snapshot_time_str) for r in page]

    return SearchVMsOutput(
        vms=vms,
        total=total,
        snapshot_time=snapshot_time_dt,
        coverage_warning=coverage_warning,
    ).model_dump(mode="json")


async def inventory_get_resource_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    inp = GetResourceInput.model_validate(input_data)
    result = await db.execute(
        select(ResourceRow).where(
            ResourceRow.id == inp.resource_id,  # type: ignore[arg-type]
            ResourceRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise KeyError(f"Resource '{inp.resource_id}' not found")

    resource_dict: dict[str, Any] = {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "connection_id": str(row.connection_id),
        "snapshot_id": str(row.snapshot_id) if row.snapshot_id else None,
        "provider": row.provider,
        "native_id": row.native_id,
        "account": row.account,
        "region": row.region,
        "zone": row.zone,
        "kind": row.kind,
        "name": row.name,
        "status": row.status,
        "tags": row.tags,
        "spec": row.spec,
        "provenance": row.provenance,
        "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
    }
    return GetResourceOutput(resource=resource_dict).model_dump()


async def inventory_diff_snapshots_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    inp = DiffSnapshotsInput.model_validate(input_data)

    async def _get_native_ids(snapshot_id: str) -> dict[str, str]:
        """Return mapping native_id -> internal resource id for a snapshot."""
        result = await db.execute(
            select(ResourceRow.native_id, ResourceRow.id).where(
                ResourceRow.snapshot_id == snapshot_id,  # type: ignore[arg-type]
                ResourceRow.workspace_id == user.workspace_id,  # type: ignore[arg-type]
            )
        )
        return {row[0]: str(row[1]) for row in result.all()}

    a_map = await _get_native_ids(inp.snapshot_id_a)
    b_map = await _get_native_ids(inp.snapshot_id_b)

    a_ids = set(a_map)
    b_ids = set(b_map)

    added = [b_map[nid] for nid in b_ids - a_ids]
    removed = [a_map[nid] for nid in a_ids - b_ids]

    # For "changed" we compare resources present in both by re-fetching specs
    common_native_ids = a_ids & b_ids
    changed: list[str] = []
    for nid in common_native_ids:
        res_a = await db.execute(
            select(ResourceRow.spec, ResourceRow.status).where(
                ResourceRow.id == a_map[nid]  # type: ignore[arg-type]
            )
        )
        res_b = await db.execute(
            select(ResourceRow.spec, ResourceRow.status).where(
                ResourceRow.id == b_map[nid]  # type: ignore[arg-type]
            )
        )
        row_a = res_a.one_or_none()
        row_b = res_b.one_or_none()
        if row_a and row_b and (row_a[0] != row_b[0] or row_a[1] != row_b[1]):
            changed.append(b_map[nid])

    return DiffSnapshotsOutput(added=added, removed=removed, changed=changed).model_dump()
