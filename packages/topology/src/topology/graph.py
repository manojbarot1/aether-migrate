"""Topology graph building and Mermaid diagram generation.

Public API:
    build_subgraph(db, resource_id, depth, direction, snapshot_id, max_nodes)
    build_region_graph(db, workspace_id, region, connection_id, snapshot_id, max_nodes)
    generate_mermaid(nodes, edges)
"""

from __future__ import annotations

import re
import uuid
from collections import deque
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Shared schema models (used by both the API router and the AI tool)
# ---------------------------------------------------------------------------

EDGE_LABELS: dict[str, str] = {
    "attached_to": "attached to",
    "in_subnet": "in subnet",
    "protected_by": "protected by",
    "behind_lb": "behind LB",
    "routes_to": "routes to",
    "depends_on": "depends on",
}


class TopologyNode(BaseModel):
    id: str
    native_id: str
    name: str
    kind: str       # vm, disk, nic, subnet, security_group, load_balancer, network
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_node(row: Any, root_id: str) -> TopologyNode:
    """Convert a ResourceRow to a TopologyNode."""
    spec: dict[str, Any] = row.spec or {}
    # Build kind-specific metadata
    kind = row.kind
    if kind == "vm":
        meta = {k: spec[k] for k in ("vcpu", "memory_gib", "os_name") if k in spec}
    elif kind == "subnet":
        meta = {k: spec[k] for k in ("cidr", "availability_zone") if k in spec}
    elif kind == "security_group":
        meta = {k: spec[k] for k in ("rules_count",) if k in spec}
    elif kind == "disk":
        meta = {k: spec[k] for k in ("size_gib", "volume_type") if k in spec}
    elif kind in ("network", "vpc"):
        meta = {k: spec[k] for k in ("cidr",) if k in spec}
    elif kind == "load_balancer":
        meta = {k: spec[k] for k in ("scheme", "dns_name") if k in spec}
    else:
        meta = {}

    return TopologyNode(
        id=str(row.id),
        native_id=row.native_id,
        name=row.name or row.native_id,
        kind=kind,
        provider=row.provider,
        region=row.region,
        status=row.status,
        is_root=(str(row.id) == root_id),
        metadata=meta,
    )


def _edge_label(kind: str) -> str:
    return EDGE_LABELS.get(kind, kind.replace("_", " "))


# ---------------------------------------------------------------------------
# Core BFS graph builder
# ---------------------------------------------------------------------------


async def build_subgraph(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    resource_id: uuid.UUID,
    depth: int,
    direction: str,
    snapshot_id: uuid.UUID | None,
    max_nodes: int = 100,
) -> tuple[list[TopologyNode], list[TopologyEdge], bool]:
    """BFS from *resource_id* up to *depth* hops.

    Parameters
    ----------
    db:
        Async SQLAlchemy session.
    workspace_id:
        Scopes all queries to the workspace.
    resource_id:
        UUID of the root resource.
    depth:
        Number of hops to traverse (capped to 3 by callers).
    direction:
        "in", "out", or "both".
    snapshot_id:
        If provided, filter edges to this snapshot; otherwise use all edges in
        the workspace for the resource's connection.
    max_nodes:
        Hard cap on the number of nodes returned.

    Returns
    -------
    (nodes, edges, truncated)
    """
    from db.models import ResourceEdgeRow, ResourceRow

    root_str = str(resource_id)
    visited: set[str] = set()
    frontier: deque[str] = deque([root_str])
    raw_edges: list[tuple[str, str, str]] = []  # (from_id, to_id, kind)

    for _hop in range(depth):
        if not frontier:
            break
        next_frontier: set[str] = set()

        while frontier:
            rid = frontier.popleft()
            if rid in visited:
                continue
            visited.add(rid)

            rid_uuid: Any = uuid.UUID(rid)

            if direction in ("out", "both"):
                q = select(ResourceEdgeRow).where(
                    ResourceEdgeRow.from_id == rid_uuid,
                    ResourceEdgeRow.workspace_id == workspace_id,
                )
                if snapshot_id is not None:
                    q = q.where(ResourceEdgeRow.snapshot_id == snapshot_id)
                result = await db.execute(q)
                for e in result.scalars().all():
                    to_str = str(e.to_id)
                    raw_edges.append((str(e.from_id), to_str, e.kind))
                    if to_str not in visited:
                        next_frontier.add(to_str)

            if direction in ("in", "both"):
                q2 = select(ResourceEdgeRow).where(
                    ResourceEdgeRow.to_id == rid_uuid,
                    ResourceEdgeRow.workspace_id == workspace_id,
                )
                if snapshot_id is not None:
                    q2 = q2.where(ResourceEdgeRow.snapshot_id == snapshot_id)
                result2 = await db.execute(q2)
                for e in result2.scalars().all():
                    from_str = str(e.from_id)
                    raw_edges.append((from_str, str(e.to_id), e.kind))
                    if from_str not in visited:
                        next_frontier.add(from_str)

        frontier = deque(next_frontier - visited)

    # Collect all node IDs referenced (edges + root + visited)
    all_node_ids: set[str] = {root_str} | visited
    for f, t, _ in raw_edges:
        all_node_ids.add(f)
        all_node_ids.add(t)

    truncated = len(all_node_ids) > max_nodes
    if truncated:
        # Keep root + first (max_nodes-1) alphabetically for determinism
        kept = {root_str} | set(sorted(all_node_ids - {root_str})[: max_nodes - 1])
        all_node_ids = kept

    # Batch-load ResourceRows
    rows_result = await db.execute(
        select(ResourceRow).where(
            ResourceRow.id.in_(
                [uuid.UUID(nid) for nid in all_node_ids]
            ),
            ResourceRow.workspace_id == workspace_id,
        )
    )
    row_map: dict[str, Any] = {str(r.id): r for r in rows_result.scalars().all()}

    nodes = [_row_to_node(row, root_str) for row in row_map.values()]

    # Deduplicate edges, keep only edges where both endpoints are loaded
    seen: set[tuple[str, str, str]] = set()
    edges: list[TopologyEdge] = []
    for f, t, k in raw_edges:
        key = (f, t, k)
        if key not in seen and f in row_map and t in row_map:
            seen.add(key)
            edges.append(TopologyEdge(from_id=f, to_id=t, kind=k, label=_edge_label(k)))

    return nodes, edges, truncated


async def build_region_graph(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    region: str,
    connection_id: uuid.UUID | None,
    snapshot_id: uuid.UUID | None,
    max_nodes: int = 200,
) -> tuple[list[TopologyNode], list[TopologyEdge], bool, uuid.UUID | None, datetime | None]:
    """Return all resources in a region and edges between them.

    Returns (nodes, edges, truncated, snapshot_id, snapshot_time).
    """
    from db.models import ResourceEdgeRow, ResourceRow, SnapshotRow

    # Resolve snapshot
    effective_snapshot_id = snapshot_id
    snap_time: datetime | None = None
    if effective_snapshot_id is None and connection_id is not None:
        q = (
            select(SnapshotRow)
            .where(
                SnapshotRow.workspace_id == workspace_id,
                SnapshotRow.connection_id == connection_id,
                SnapshotRow.status == "completed",
            )
            .order_by(SnapshotRow.completed_at.desc())
            .limit(1)
        )
        r = await db.execute(q)
        snap_row = r.scalar_one_or_none()
        if snap_row:
            effective_snapshot_id = snap_row.id
            snap_time = snap_row.completed_at
    elif effective_snapshot_id is not None:
        r2 = await db.execute(select(SnapshotRow).where(SnapshotRow.id == effective_snapshot_id))
        snap_row2 = r2.scalar_one_or_none()
        if snap_row2:
            snap_time = snap_row2.completed_at

    rq = select(ResourceRow).where(
        ResourceRow.workspace_id == workspace_id,
        ResourceRow.region == region,
    )
    if effective_snapshot_id is not None:
        rq = rq.where(ResourceRow.snapshot_id == effective_snapshot_id)
    if connection_id is not None:
        rq = rq.where(ResourceRow.connection_id == connection_id)

    rq = rq.limit(max_nodes + 1)
    res = await db.execute(rq)
    all_rows = res.scalars().all()

    truncated = len(all_rows) > max_nodes
    rows_to_use = all_rows[:max_nodes]
    node_ids: set[str] = {str(r.id) for r in rows_to_use}
    row_map: dict[str, Any] = {str(r.id): r for r in rows_to_use}
    root_str = ""  # no single root for region view

    nodes = [_row_to_node(row, root_str) for row in rows_to_use]

    # Load edges between nodes in this set
    eq = select(ResourceEdgeRow).where(
        ResourceEdgeRow.workspace_id == workspace_id,
        ResourceEdgeRow.from_id.in_([uuid.UUID(nid) for nid in node_ids]),
    )
    if effective_snapshot_id is not None:
        eq = eq.where(ResourceEdgeRow.snapshot_id == effective_snapshot_id)
    eres = await db.execute(eq)

    edges: list[TopologyEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for e in eres.scalars().all():
        f, t, k = str(e.from_id), str(e.to_id), e.kind
        if t in node_ids and (f, t, k) not in seen:
            seen.add((f, t, k))
            edges.append(TopologyEdge(from_id=f, to_id=t, kind=k, label=_edge_label(k)))

    return nodes, edges, truncated, effective_snapshot_id, snap_time


# ---------------------------------------------------------------------------
# Mermaid generation
# ---------------------------------------------------------------------------

_MERMAID_UNSAFE = re.compile(r'["\[\]{}<>()|&;\'`#%^*!@$]')
_WHITESPACE_RUN = re.compile(r"\s{2,}")
_MAX_LABEL_LEN = 50
_MAX_MERMAID_NODES = 30


def _sanitize_label(text: str) -> str:
    """Strip Mermaid-unsafe characters and truncate to 50 chars."""
    cleaned = _MERMAID_UNSAFE.sub(" ", text)
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip()
    if len(cleaned) > _MAX_LABEL_LEN:
        cleaned = cleaned[: _MAX_LABEL_LEN - 1] + "…"
    return cleaned


def _node_shape(kind: str, node_id: str, label: str) -> str:
    """Return a Mermaid node definition with the shape for *kind*."""
    safe = label
    # Unique short alias — use first 8 chars of id (no hyphens)
    alias = "n" + node_id.replace("-", "")[:8]
    if kind == "vm":
        return f'    {alias}["{safe}"]'
    elif kind in ("subnet",):
        return f'    {alias}{{{{"{safe}"}}}}'
    elif kind == "security_group":
        return f'    {alias}[/"{safe}"\\]'
    elif kind == "disk":
        return f'    {alias}[("{safe}")]'
    elif kind == "nic":
        return f'    {alias}(("{safe}"))'
    elif kind in ("load_balancer", "lb"):
        return f'    {alias}{{"{safe}"}}'
    else:
        # network, vpc, unknown → rectangle
        return f'    {alias}["{safe}"]'


def generate_mermaid(nodes: list[TopologyNode], edges: list[TopologyEdge]) -> str:
    """Generate a Mermaid flowchart string from *nodes* and *edges*.

    If there are more than 30 nodes, a truncation message is prepended as a
    comment and only the first 30 nodes are rendered.
    """
    truncated_msg = ""
    display_nodes = nodes
    if len(nodes) > _MAX_MERMAID_NODES:
        truncated_msg = (
            f"%% NOTE: diagram truncated to {_MAX_MERMAID_NODES} of {len(nodes)} nodes\n"
        )
        # Always keep root node first
        roots = [n for n in nodes if n.is_root]
        others = [n for n in nodes if not n.is_root]
        display_nodes = (roots + others)[:_MAX_MERMAID_NODES]

    # Build alias map
    node_set = {n.id for n in display_nodes}
    alias_map: dict[str, str] = {}
    for n in display_nodes:
        alias_map[n.id] = "n" + n.id.replace("-", "")[:8]

    lines: list[str] = []
    if truncated_msg:
        lines.append(truncated_msg.rstrip())

    lines.append("flowchart LR")

    for n in display_nodes:
        label = _sanitize_label(n.name or n.native_id)
        lines.append(_node_shape(n.kind, n.id, label))

    for e in edges:
        if e.from_id not in node_set or e.to_id not in node_set:
            continue
        fa = alias_map[e.from_id]
        ta = alias_map[e.to_id]
        safe_label = _sanitize_label(e.label)
        lines.append(f'    {fa} -->|"{safe_label}"| {ta}')

    return "\n".join(lines)
