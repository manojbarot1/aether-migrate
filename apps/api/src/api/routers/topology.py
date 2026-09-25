"""Topology router — subgraph, region view, Mermaid export, and snapshot diff.

All endpoints are read-only and require at least the ``viewer`` role.

Routes:
    GET /topology/resources/{resource_id}  — BFS subgraph centred on a resource
    GET /topology/regions/{region}         — all resources in a region
    GET /topology/export/{resource_id}     — Mermaid diagram as text/plain
    GET /topology/diff/{snap_a}/{snap_b}   — structural diff between two snapshots
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/topology", tags=["topology"])

_MAX_DEPTH = 3
_MAX_NODES = 100
_MAX_REGION_NODES = 200


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TopologyNode(BaseModel):
    id: str
    native_id: str
    name: str
    kind: str
    provider: str
    region: str
    status: str
    is_root: bool
    metadata: dict[str, Any]


class TopologyEdge(BaseModel):
    from_id: str
    to_id: str
    kind: str
    label: str


class TopologyGraphResponse(BaseModel):
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
    root_id: str
    snapshot_id: str | None
    snapshot_time: datetime | None
    truncated: bool


class TopologyDiffResponse(BaseModel):
    added_nodes: list[TopologyNode]
    removed_nodes: list[TopologyNode]
    changed_nodes: list[TopologyNode]
    added_edges: list[TopologyEdge]
    removed_edges: list[TopologyEdge]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_snapshot(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    resource_id: uuid.UUID | None,
    snapshot_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, datetime | None]:
    """Return (snapshot_id, snapshot_time) — defaults to latest for the resource's connection."""
    from db.models import ResourceRow, SnapshotRow

    if snapshot_id is not None:
        r = await db.execute(select(SnapshotRow).where(SnapshotRow.id == snapshot_id))
        row = r.scalar_one_or_none()
        return snapshot_id, (row.completed_at if row else None)

    if resource_id is None:
        return None, None

    # Find the resource's connection, then find the latest completed snapshot for it
    rr = await db.execute(
        select(ResourceRow).where(
            ResourceRow.id == resource_id,
            ResourceRow.workspace_id == workspace_id,
        )
    )
    resource = rr.scalar_one_or_none()
    if resource is None or resource.snapshot_id is None:
        return None, None

    snap_r = await db.execute(
        select(SnapshotRow).where(SnapshotRow.id == resource.snapshot_id)
    )
    snap = snap_r.scalar_one_or_none()
    return (resource.snapshot_id, snap.completed_at if snap else None)


def _pkg_to_api_node(pkg_node: Any) -> TopologyNode:
    return TopologyNode(
        id=pkg_node.id,
        native_id=pkg_node.native_id,
        name=pkg_node.name,
        kind=pkg_node.kind,
        provider=pkg_node.provider,
        region=pkg_node.region,
        status=pkg_node.status,
        is_root=pkg_node.is_root,
        metadata=pkg_node.metadata,
    )


def _pkg_to_api_edge(pkg_edge: Any) -> TopologyEdge:
    return TopologyEdge(
        from_id=pkg_edge.from_id,
        to_id=pkg_edge.to_id,
        kind=pkg_edge.kind,
        label=pkg_edge.label,
    )


# ---------------------------------------------------------------------------
# GET /topology/resources/{resource_id}
# ---------------------------------------------------------------------------


@router.get("/resources/{resource_id}", response_model=TopologyGraphResponse)
async def get_resource_subgraph(
    resource_id: uuid.UUID,
    depth: int = Query(default=2, ge=1, le=_MAX_DEPTH, description="BFS depth (max 3)"),
    direction: str = Query(
        default="both",
        description="Edge direction to follow: 'in', 'out', or 'both'",
    ),
    snapshot_id: uuid.UUID | None = Query(default=None),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> TopologyGraphResponse:
    """Return a subgraph centred on *resource_id* via BFS."""
    if direction not in ("in", "out", "both"):
        raise HTTPException(status_code=422, detail="direction must be 'in', 'out', or 'both'")

    from topology.graph import build_subgraph

    effective_snap_id, snap_time = await _resolve_snapshot(
        session, workspace_id, resource_id, snapshot_id
    )

    nodes_pkg, edges_pkg, truncated = await build_subgraph(
        db=session,
        workspace_id=workspace_id,
        resource_id=resource_id,
        depth=depth,
        direction=direction,
        snapshot_id=effective_snap_id,
        max_nodes=_MAX_NODES,
    )

    if not nodes_pkg:
        raise HTTPException(status_code=404, detail="Resource not found")

    return TopologyGraphResponse(
        nodes=[_pkg_to_api_node(n) for n in nodes_pkg],
        edges=[_pkg_to_api_edge(e) for e in edges_pkg],
        root_id=str(resource_id),
        snapshot_id=str(effective_snap_id) if effective_snap_id else None,
        snapshot_time=snap_time,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# GET /topology/regions/{region}
# ---------------------------------------------------------------------------


@router.get("/regions/{region}", response_model=TopologyGraphResponse)
async def get_region_graph(
    region: str,
    connection_id: uuid.UUID | None = Query(default=None),
    snapshot_id: uuid.UUID | None = Query(default=None),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> TopologyGraphResponse:
    """Return all resources in *region* as a graph (limited to 200 nodes)."""
    from topology.graph import build_region_graph

    nodes_pkg, edges_pkg, truncated, eff_snap_id, snap_time = await build_region_graph(
        db=session,
        workspace_id=workspace_id,
        region=region,
        connection_id=connection_id,
        snapshot_id=snapshot_id,
        max_nodes=_MAX_REGION_NODES,
    )

    return TopologyGraphResponse(
        nodes=[_pkg_to_api_node(n) for n in nodes_pkg],
        edges=[_pkg_to_api_edge(e) for e in edges_pkg],
        root_id="",
        snapshot_id=str(eff_snap_id) if eff_snap_id else None,
        snapshot_time=snap_time,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# GET /topology/export/{resource_id}
# ---------------------------------------------------------------------------


@router.get("/export/{resource_id}", response_class=PlainTextResponse)
async def export_mermaid(
    resource_id: uuid.UUID,
    format: str = Query(default="mermaid", description="Export format (only 'mermaid' supported)"),
    depth: int = Query(default=2, ge=1, le=_MAX_DEPTH),
    snapshot_id: uuid.UUID | None = Query(default=None),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> str:
    """Export a Mermaid flowchart for the subgraph centred on *resource_id*."""
    if format != "mermaid":
        raise HTTPException(status_code=422, detail="Only 'mermaid' format is supported")

    from topology.graph import build_subgraph, generate_mermaid

    effective_snap_id, _ = await _resolve_snapshot(
        session, workspace_id, resource_id, snapshot_id
    )

    nodes_pkg, edges_pkg, _ = await build_subgraph(
        db=session,
        workspace_id=workspace_id,
        resource_id=resource_id,
        depth=depth,
        direction="both",
        snapshot_id=effective_snap_id,
        max_nodes=_MAX_NODES,
    )

    if not nodes_pkg:
        raise HTTPException(status_code=404, detail="Resource not found")

    return generate_mermaid(nodes_pkg, edges_pkg)


# ---------------------------------------------------------------------------
# GET /topology/diff/{snapshot_id_a}/{snapshot_id_b}
# ---------------------------------------------------------------------------


@router.get("/diff/{snapshot_id_a}/{snapshot_id_b}", response_model=TopologyDiffResponse)
async def topology_diff(
    snapshot_id_a: uuid.UUID,
    snapshot_id_b: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> TopologyDiffResponse:
    """Return structural diff between two topology snapshots."""
    from db.models import ResourceEdgeRow, ResourceRow
    from topology.graph import EDGE_LABELS, _row_to_node  # noqa: PLC2701

    async def _load_snapshot(
        snap_id: uuid.UUID,
    ) -> tuple[dict[str, Any], dict[tuple[str, str, str], str]]:
        """Return (node_map_by_native_id -> row, edge_set)."""
        rr = await session.execute(
            select(ResourceRow).where(
                ResourceRow.workspace_id == workspace_id,
                ResourceRow.snapshot_id == snap_id,
            )
        )
        rows = rr.scalars().all()
        node_map: dict[str, Any] = {r.native_id: r for r in rows}
        node_id_map: dict[str, str] = {str(r.id): r.native_id for r in rows}

        er = await session.execute(
            select(ResourceEdgeRow).where(
                ResourceEdgeRow.workspace_id == workspace_id,
                ResourceEdgeRow.snapshot_id == snap_id,
            )
        )
        edge_set: dict[tuple[str, str, str], str] = {}
        for e in er.scalars().all():
            f_nat = node_id_map.get(str(e.from_id))
            t_nat = node_id_map.get(str(e.to_id))
            if f_nat and t_nat:
                edge_set[(f_nat, t_nat, e.kind)] = e.kind

        return node_map, edge_set

    nodes_a, edges_a = await _load_snapshot(snapshot_id_a)
    nodes_b, edges_b = await _load_snapshot(snapshot_id_b)

    set_a = set(nodes_a.keys())
    set_b = set(nodes_b.keys())

    dummy_root = ""

    added_nodes = [
        _pkg_to_api_node(_row_to_node(nodes_b[nid], dummy_root))
        for nid in sorted(set_b - set_a)
    ]
    removed_nodes = [
        _pkg_to_api_node(_row_to_node(nodes_a[nid], dummy_root))
        for nid in sorted(set_a - set_b)
    ]
    changed_nodes = [
        _pkg_to_api_node(_row_to_node(nodes_b[nid], dummy_root))
        for nid in sorted(set_a & set_b)
        if nodes_a[nid].spec != nodes_b[nid].spec
    ]

    edges_a_keys = set(edges_a.keys())
    edges_b_keys = set(edges_b.keys())

    def _edge_from_key(key: tuple[str, str, str]) -> TopologyEdge:
        f_nat, t_nat, k = key
        return TopologyEdge(
            from_id=f_nat,
            to_id=t_nat,
            kind=k,
            label=EDGE_LABELS.get(k, k.replace("_", " ")),
        )

    added_edges = [_edge_from_key(k) for k in sorted(edges_b_keys - edges_a_keys)]
    removed_edges = [_edge_from_key(k) for k in sorted(edges_a_keys - edges_b_keys)]

    return TopologyDiffResponse(
        added_nodes=added_nodes,
        removed_nodes=removed_nodes,
        changed_nodes=changed_nodes,
        added_edges=added_edges,
        removed_edges=removed_edges,
    )
