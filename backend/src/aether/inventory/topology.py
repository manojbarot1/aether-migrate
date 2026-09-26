"""Topology view of one network (VPC/VNet): nodes nested as network → subnet → VM,
plus load balancers and security groups, with typed edges. Pure over DB rows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.core.inventory import EdgeKind, ResourceType
from aether.db.models import Resource, ResourceEdge

MAX_VMS = 400
_TYPES = (
    ResourceType.NETWORK,
    ResourceType.SUBNET,
    ResourceType.VM,
    ResourceType.LOAD_BALANCER,
    ResourceType.SECURITY_GROUP,
)


@dataclass
class TopologyNode:
    id: uuid.UUID
    type: str
    native_id: str
    name: str | None
    parent: uuid.UUID | None
    status: str | None
    label_detail: str | None


@dataclass
class Topology:
    network: TopologyNode
    nodes: list[TopologyNode] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


def _node(r: Resource, parent: uuid.UUID | None) -> TopologyNode:
    detail = None
    if r.type == ResourceType.VM:
        detail = r.spec.get("source_sku")
    elif r.type == ResourceType.SUBNET:
        detail = r.spec.get("cidr")
    elif r.type == ResourceType.LOAD_BALANCER:
        detail = f"{r.spec.get('kind') or ''} {r.spec.get('scheme') or ''}".strip() or None
    elif r.type == ResourceType.NETWORK:
        detail = ", ".join(r.spec.get("cidrs") or []) or None
    return TopologyNode(r.id, r.type, r.native_id, r.name, parent, r.status, detail)


async def network_topology(
    session: AsyncSession, network_id: uuid.UUID, *, include_security_groups: bool = True
) -> Topology | None:
    # The network's resource id pins the snapshot, so a topology is always internally consistent.
    net = await session.get(Resource, network_id)
    if net is None or net.type != ResourceType.NETWORK:
        return None
    rows = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.snapshot_id == net.snapshot_id,
                    Resource.region == net.region,
                    Resource.type.in_([t.value for t in _TYPES]),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {r.id: r for r in rows}
    edge_rows = (
        (
            await session.execute(
                select(ResourceEdge).where(
                    ResourceEdge.snapshot_id == net.snapshot_id,
                    ResourceEdge.from_id.in_(by_id.keys()),
                    ResourceEdge.to_id.in_(by_id.keys()),
                )
            )
        )
        .scalars()
        .all()
    )

    vpc = net.native_id
    subnets = {r.id for r in rows if r.type == ResourceType.SUBNET and r.spec.get("network_native_id") == vpc}
    vm_parent: dict[uuid.UUID, uuid.UUID] = {}
    for e in edge_rows:
        if e.kind == EdgeKind.IN_SUBNET and e.to_id in subnets and by_id[e.from_id].type == ResourceType.VM:
            vm_parent[e.from_id] = e.to_id

    topo = Topology(network=_node(net, None))
    topo.nodes.append(topo.network)
    topo.nodes += [_node(by_id[s], net.id) for s in sorted(subnets, key=lambda i: by_id[i].name or "")]
    vms = sorted(vm_parent, key=lambda i: by_id[i].name or by_id[i].native_id)
    if len(vms) > MAX_VMS:
        topo.truncated = True
        vms = vms[:MAX_VMS]
    topo.nodes += [_node(by_id[v], vm_parent[v]) for v in vms]
    lbs = [r for r in rows if r.type == ResourceType.LOAD_BALANCER and r.spec.get("network_native_id") == vpc]
    topo.nodes += [_node(r, net.id) for r in lbs]
    if include_security_groups:
        sgs = [
            r
            for r in rows
            if r.type == ResourceType.SECURITY_GROUP and r.spec.get("network_native_id") == vpc
        ]
        topo.nodes += [_node(r, net.id) for r in sgs]

    included = {n.id for n in topo.nodes}
    for e in edge_rows:
        if (
            e.from_id in included
            and e.to_id in included
            and e.kind
            in (
                EdgeKind.ROUTES_TO,
                EdgeKind.PROTECTED_BY,
                EdgeKind.REFERENCES,
            )
        ):
            if e.kind == EdgeKind.PROTECTED_BY and not include_security_groups:
                continue
            topo.edges.append({"from": str(e.from_id), "to": str(e.to_id), "kind": e.kind})
    return topo


def to_mermaid(topo: Topology) -> str:
    """Mermaid flowchart for documents and plan exports."""

    def nid(u: uuid.UUID) -> str:
        return "n" + u.hex[:12]

    def label(n: TopologyNode) -> str:
        text = (n.name or n.native_id).replace('"', "'")
        return f"{text}<br/><small>{n.label_detail}</small>" if n.label_detail else text

    children: dict[uuid.UUID | None, list[TopologyNode]] = {}
    for n in topo.nodes:
        children.setdefault(n.parent, []).append(n)

    lines = ["flowchart LR"]

    def emit(n: TopologyNode, indent: str) -> None:
        kids = children.get(n.id, [])
        if n.type in (ResourceType.NETWORK, ResourceType.SUBNET):
            lines.append(f'{indent}subgraph {nid(n.id)}["{label(n)}"]')
            for k in kids:
                emit(k, indent + "  ")
            lines.append(f"{indent}end")
        else:
            shape = (
                ("[/", "/]")
                if n.type == ResourceType.LOAD_BALANCER
                else ("{{", "}}")
                if n.type == ResourceType.SECURITY_GROUP
                else ("[", "]")
            )
            lines.append(f'{indent}{nid(n.id)}{shape[0]}"{label(n)}"{shape[1]}')

    emit(topo.network, "  ")
    arrows = {"routes_to": "-->", "protected_by": "-.->", "references": "-.->"}
    for e in topo.edges:
        src, dst = nid(uuid.UUID(e["from"])), nid(uuid.UUID(e["to"]))
        lines.append(f"  {src} {arrows[e['kind']]}|{e['kind']}| {dst}")
    return "\n".join(lines) + "\n"
