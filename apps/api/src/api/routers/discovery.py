"""Discovery router — trigger discovery workflows and query snapshot state."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/discovery", tags=["discovery"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DiscoveryStartResponse(BaseModel):
    job_id: str
    snapshot_id: str


class SnapshotResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    coverage: dict[str, Any]


class ConnectionStatusResponse(BaseModel):
    connection_id: uuid.UUID
    latest_snapshot: SnapshotResponse | None
    last_discovery_time: datetime | None


# ---------------------------------------------------------------------------
# Temporal client helper
# ---------------------------------------------------------------------------


async def _get_temporal_client() -> Any:
    """Return a connected Temporal client (lazy import for worker isolation)."""
    from temporalio.client import Client

    host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    return await Client.connect(host, namespace=namespace)


def _snap_row_to_response(row: Any) -> SnapshotResponse:
    return SnapshotResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        provider=row.provider,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        coverage=row.coverage,
    )


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _record_audit(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user: AuthenticatedUser,
    action: str,
    target_id: str,
    connection_id: uuid.UUID | None = None,
) -> None:
    from audit.models import AuditEvent
    from audit.writer import AuditWriter

    event = AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=action,
        target_type="Discovery",
        target_id=target_id,
        connection_id=connection_id,
        result_status="success",
    )
    writer = AuditWriter(workspace_id=workspace_id, session_factory=lambda: session)
    await writer.record(event)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/connections/{connection_id}/refresh", response_model=DiscoveryStartResponse)
async def refresh_connection(
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> DiscoveryStartResponse:
    """Start an AWSDiscoveryWorkflow for the given connection.

    Returns ``job_id`` (Temporal workflow ID) and ``snapshot_id`` (pre-created
    snapshot UUID that the workflow will update).
    """
    from aws.discovery import AWSDiscoveryInput
    from db.models import ConnectionRow

    # Verify connection belongs to workspace
    result = await session.execute(
        select(ConnectionRow).where(
            ConnectionRow.id == connection_id,
            ConnectionRow.workspace_id == workspace_id,
        )
    )
    conn_row = result.scalar_one_or_none()
    if conn_row is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Only AWS supported in Phase 2a
    if conn_row.provider != "aws":
        raise HTTPException(
            status_code=422,
            detail=f"Discovery not yet supported for provider '{conn_row.provider}'",
        )

    job_id = f"discovery-{connection_id}-{uuid.uuid4().hex[:8]}"

    try:
        client = await _get_temporal_client()
        handle = await client.start_workflow(
            "AWSDiscoveryWorkflow",
            AWSDiscoveryInput(
                connection_id=str(connection_id),
                workspace_id=str(workspace_id),
            ),
            id=job_id,
            task_queue="connector",
        )
        snapshot_id = handle.id  # workflow run-id serves as correlation
    except Exception as exc:
        log.error(
            "discovery.start.failed",
            connection_id=str(connection_id),
            error=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Failed to start discovery workflow") from exc

    await _record_audit(
        session, workspace_id, user, "discovery.start",
        str(connection_id), connection_id,
    )

    log.info(
        "discovery.started",
        job_id=job_id,
        connection_id=str(connection_id),
    )
    return DiscoveryStartResponse(job_id=job_id, snapshot_id=job_id)


@router.get("/snapshots", response_model=list[SnapshotResponse])
async def list_snapshots(
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> list[SnapshotResponse]:
    """List discovery snapshots for the workspace (newest first)."""
    from db.models import SnapshotRow

    result = await session.execute(
        select(SnapshotRow)
        .where(SnapshotRow.workspace_id == workspace_id)
        .order_by(SnapshotRow.started_at.desc())
        .limit(100)
    )
    rows = result.scalars().all()
    return [_snap_row_to_response(r) for r in rows]


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot(
    snapshot_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> SnapshotResponse:
    """Get snapshot detail with coverage report."""
    from db.models import SnapshotRow

    result = await session.execute(
        select(SnapshotRow).where(
            SnapshotRow.id == snapshot_id,
            SnapshotRow.workspace_id == workspace_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return _snap_row_to_response(row)


@router.get("/connections/{connection_id}/status", response_model=ConnectionStatusResponse)
async def get_connection_status(
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> ConnectionStatusResponse:
    """Return the latest snapshot for a connection and last discovery time."""
    from db.models import SnapshotRow

    result = await session.execute(
        select(SnapshotRow)
        .where(
            SnapshotRow.connection_id == connection_id,
            SnapshotRow.workspace_id == workspace_id,
        )
        .order_by(SnapshotRow.started_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()

    latest: SnapshotResponse | None = None
    last_discovery_time: datetime | None = None
    if row is not None:
        latest = _snap_row_to_response(row)
        last_discovery_time = row.completed_at or row.started_at

    return ConnectionStatusResponse(
        connection_id=connection_id,
        latest_snapshot=latest,
        last_discovery_time=last_discovery_time,
    )
