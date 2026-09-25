"""Snapshots router — list and retrieve discovery snapshots."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/snapshots", tags=["snapshots"])


class SnapshotResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    started_at: Any
    completed_at: Any | None
    status: str
    coverage: dict[str, Any]


@router.get("", response_model=list[SnapshotResponse])
async def list_snapshots(
    connection_id: uuid.UUID | None = Query(default=None, description="Filter by connection"),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> list[SnapshotResponse]:
    from db.models import SnapshotRow

    q = select(SnapshotRow).where(SnapshotRow.workspace_id == workspace_id)
    if connection_id:
        q = q.where(SnapshotRow.connection_id == connection_id)
    q = q.order_by(SnapshotRow.started_at.desc()).limit(100)

    result = await session.execute(q)
    rows = result.scalars().all()
    return [
        SnapshotResponse(
            id=r.id,
            workspace_id=r.workspace_id,
            connection_id=r.connection_id,
            provider=r.provider,
            started_at=r.started_at,
            completed_at=r.completed_at,
            status=r.status,
            coverage=r.coverage,
        )
        for r in rows
    ]


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot(
    snapshot_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> SnapshotResponse:
    from db.models import SnapshotRow
    from fastapi import HTTPException

    result = await session.execute(
        select(SnapshotRow).where(
            SnapshotRow.id == snapshot_id,
            SnapshotRow.workspace_id == workspace_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return SnapshotResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        provider=row.provider,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        coverage=row.coverage,
    )
