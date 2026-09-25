"""Inventory router — query normalized VM and resource data.

All queries are workspace-scoped. Snapshot defaults to the latest completed
snapshot per connection when no snapshot_id is supplied.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/inventory", tags=["inventory"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ResourceSummary(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    native_id: str
    account: str
    region: str
    zone: str | None
    kind: str
    name: str | None
    status: str
    tags: dict[str, Any]
    spec: dict[str, Any]
    snapshot_id: uuid.UUID | None
    snapshot_time: datetime | None
    discovered_at: Any


class VMListResponse(BaseModel):
    items: list[ResourceSummary]
    total: int
    snapshot_id: uuid.UUID | None
    snapshot_time: datetime | None


class ResourceEdgeResponse(BaseModel):
    from_id: uuid.UUID
    to_id: uuid.UUID
    kind: str
    snapshot_id: uuid.UUID | None


class ResourceDetailResponse(BaseModel):
    resource: ResourceSummary
    raw_ref: str | None
    provenance: dict[str, Any]
    edges_out: list[ResourceEdgeResponse]
    edges_in: list[ResourceEdgeResponse]


class DiffResponse(BaseModel):
    snapshot_id_a: uuid.UUID
    snapshot_id_b: uuid.UUID
    added: list[str]       # native_ids present in B but not A
    removed: list[str]     # native_ids present in A but not B
    changed: list[str]     # native_ids present in both but spec differs


# ---------------------------------------------------------------------------
# Helper: resolve latest snapshot for workspace
# ---------------------------------------------------------------------------


async def _latest_snapshot_id(
    workspace_id: uuid.UUID,
    session: AsyncSession,
    connection_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, datetime | None]:
    """Return (snapshot_id, completed_at) for the most recent completed snapshot."""
    from db.models import SnapshotRow

    q = (
        select(SnapshotRow)
        .where(
            SnapshotRow.workspace_id == workspace_id,
            SnapshotRow.status == "completed",
        )
        .order_by(SnapshotRow.completed_at.desc())
        .limit(1)
    )
    if connection_id is not None:
        q = q.where(SnapshotRow.connection_id == connection_id)

    result = await session.execute(q)
    row = result.scalar_one_or_none()
    if row is None:
        return None, None
    return row.id, row.completed_at


def _row_to_summary(row: Any, snapshot_time: datetime | None = None) -> ResourceSummary:
    return ResourceSummary(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        provider=row.provider,
        native_id=row.native_id,
        account=row.account,
        region=row.region,
        zone=row.zone,
        kind=row.kind,
        name=row.name,
        status=row.status,
        tags=row.tags,
        spec=row.spec,
        snapshot_id=row.snapshot_id,
        snapshot_time=snapshot_time,
        discovered_at=row.discovered_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/vms", response_model=VMListResponse)
async def list_vms(
    provider: str | None = Query(default=None, description="Filter by provider"),
    region: str | None = Query(default=None, description="Filter by region"),
    status: str | None = Query(default=None, description="Filter by status"),
    min_vcpu: int | None = Query(default=None, description="Minimum vCPU count"),
    min_memory_gib: float | None = Query(default=None, description="Minimum memory GiB"),
    snapshot_id: uuid.UUID | None = Query(default=None, description="Snapshot to query (default: latest)"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> VMListResponse:
    """Query VM resources with optional filters."""
    from db.models import ResourceRow

    # Resolve snapshot
    effective_snapshot_id = snapshot_id
    snap_time: datetime | None = None
    if effective_snapshot_id is None:
        effective_snapshot_id, snap_time = await _latest_snapshot_id(workspace_id, session)
    else:
        # Fetch snapshot time for the supplied snapshot_id
        from db.models import SnapshotRow

        snap_result = await session.execute(
            select(SnapshotRow).where(SnapshotRow.id == effective_snapshot_id)
        )
        snap_row = snap_result.scalar_one_or_none()
        if snap_row is not None:
            snap_time = snap_row.completed_at

    q = select(ResourceRow).where(
        ResourceRow.workspace_id == workspace_id,
        ResourceRow.kind == "vm",
    )
    if effective_snapshot_id is not None:
        q = q.where(ResourceRow.snapshot_id == effective_snapshot_id)
    if provider:
        q = q.where(ResourceRow.provider == provider)
    if region:
        q = q.where(ResourceRow.region == region)
    if status:
        q = q.where(ResourceRow.status == status)

    # Total count query (before in-memory spec filters)
    count_q = select(func.count()).select_from(q.subquery())
    total_result = await session.execute(count_q)
    total = total_result.scalar_one() or 0

    q = q.order_by(ResourceRow.discovered_at.desc()).offset(offset).limit(limit)
    result = await session.execute(q)
    rows = result.scalars().all()

    # In-memory spec filters (Phase 2 uses generated columns for these)
    output: list[ResourceSummary] = []
    for r in rows:
        if min_vcpu is not None and (r.spec.get("vcpu") or 0) < min_vcpu:
            continue
        if min_memory_gib is not None and (r.spec.get("memory_gib") or 0) < min_memory_gib:
            continue
        output.append(_row_to_summary(r, snap_time))

    return VMListResponse(
        items=output,
        total=total,
        snapshot_id=effective_snapshot_id,
        snapshot_time=snap_time,
    )


@router.get("/resources/{resource_id}", response_model=ResourceDetailResponse)
async def get_resource(
    resource_id: uuid.UUID,
    include_raw: bool = Query(default=False, description="Include raw API response (analyst role required)"),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> ResourceDetailResponse:
    """Return full resource detail with spec, provenance, and edges."""
    from db.models import ResourceEdgeRow, ResourceRow, SnapshotRow

    result = await session.execute(
        select(ResourceRow).where(
            ResourceRow.id == resource_id,
            ResourceRow.workspace_id == workspace_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    # Snapshot time
    snap_time: datetime | None = None
    if row.snapshot_id:
        snap_result = await session.execute(
            select(SnapshotRow).where(SnapshotRow.id == row.snapshot_id)
        )
        snap_row = snap_result.scalar_one_or_none()
        if snap_row:
            snap_time = snap_row.completed_at

    summary = _row_to_summary(row, snap_time)

    # raw_ref — only exposed to analysts
    raw_ref: str | None = None
    if include_raw:
        if not user.has_role("analyst"):
            raise HTTPException(status_code=403, detail="include_raw requires analyst role")
        raw_ref = row.raw_ref

    # Edges out
    edges_out_result = await session.execute(
        select(ResourceEdgeRow).where(ResourceEdgeRow.from_id == resource_id)
    )
    edges_out = [
        ResourceEdgeResponse(
            from_id=e.from_id,
            to_id=e.to_id,
            kind=e.kind,
            snapshot_id=e.snapshot_id,
        )
        for e in edges_out_result.scalars().all()
    ]

    # Edges in
    edges_in_result = await session.execute(
        select(ResourceEdgeRow).where(ResourceEdgeRow.to_id == resource_id)
    )
    edges_in = [
        ResourceEdgeResponse(
            from_id=e.from_id,
            to_id=e.to_id,
            kind=e.kind,
            snapshot_id=e.snapshot_id,
        )
        for e in edges_in_result.scalars().all()
    ]

    return ResourceDetailResponse(
        resource=summary,
        raw_ref=raw_ref,
        provenance=row.provenance,
        edges_out=edges_out,
        edges_in=edges_in,
    )


@router.get("/diff", response_model=DiffResponse)
async def diff_snapshots(
    snapshot_id_a: uuid.UUID = Query(..., description="Base snapshot"),
    snapshot_id_b: uuid.UUID = Query(..., description="Comparison snapshot"),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> DiffResponse:
    """Compare two snapshots: return added, removed, and changed resource native_ids."""
    from db.models import ResourceRow

    async def _get_resources(snap_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        result = await session.execute(
            select(ResourceRow).where(
                ResourceRow.workspace_id == workspace_id,
                ResourceRow.snapshot_id == snap_id,
            )
        )
        return {r.native_id: r.spec for r in result.scalars().all()}

    resources_a = await _get_resources(snapshot_id_a)
    resources_b = await _get_resources(snapshot_id_b)

    set_a = set(resources_a.keys())
    set_b = set(resources_b.keys())

    added = sorted(set_b - set_a)
    removed = sorted(set_a - set_b)
    changed = sorted(
        k for k in (set_a & set_b) if resources_a[k] != resources_b[k]
    )

    return DiffResponse(
        snapshot_id_a=snapshot_id_a,
        snapshot_id_b=snapshot_id_b,
        added=added,
        removed=removed,
        changed=changed,
    )
