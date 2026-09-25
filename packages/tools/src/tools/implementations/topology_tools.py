"""topology.get — retrieve a resource and its neighbourhood as a graph.

Delegates core graph logic to ``topology.graph.build_subgraph`` so that
the same BFS algorithm is shared with the API router.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TopologyGetInput(BaseModel):
    resource_id: str
    depth: int = 2
    direction: str = "both"


class NodeSummary(BaseModel):
    id: str
    name: str | None
    kind: str
    provider: str
    region: str
    status: str
    is_root: bool


class EdgeSummary(BaseModel):
    from_id: str
    to_id: str
    kind: str
    label: str


class TopologyGetOutput(BaseModel):
    nodes: list[NodeSummary]
    edges: list[EdgeSummary]
    truncated: bool


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

TOPOLOGY_GET_TOOL = ToolDefinition(
    name="topology.get",
    description=(
        "Return a resource and its neighbourhood as a graph (nodes + edges). "
        "The depth parameter controls how many relationship hops to traverse "
        "(max 3). direction can be 'in', 'out', or 'both' (default). "
        "Useful for understanding how a VM connects to networks, "
        "security groups, and load balancers."
    ),
    input_schema=TopologyGetInput,
    output_schema=TopologyGetOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["topology"],
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def topology_get_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    from topology.graph import build_subgraph

    inp = TopologyGetInput.model_validate(input_data)
    depth = min(inp.depth, 3)
    direction = inp.direction if inp.direction in ("in", "out", "both") else "both"

    try:
        resource_id = uuid.UUID(inp.resource_id)
    except ValueError:
        return TopologyGetOutput(nodes=[], edges=[], truncated=False).model_dump()

    nodes_pkg, edges_pkg, truncated = await build_subgraph(
        db=db,
        workspace_id=uuid.UUID(str(user.workspace_id)),
        resource_id=resource_id,
        depth=depth,
        direction=direction,
        snapshot_id=None,
        max_nodes=100,
    )

    nodes = [
        NodeSummary(
            id=n.id,
            name=n.name,
            kind=n.kind,
            provider=n.provider,
            region=n.region,
            status=n.status,
            is_root=n.is_root,
        )
        for n in nodes_pkg
    ]

    edges = [
        EdgeSummary(from_id=e.from_id, to_id=e.to_id, kind=e.kind, label=e.label)
        for e in edges_pkg
    ]

    return TopologyGetOutput(nodes=nodes, edges=edges, truncated=truncated).model_dump()
