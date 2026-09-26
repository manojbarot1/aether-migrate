from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from temporalio.client import Client

from aether.api.deps import SettingsDep, WorkspaceContext, get_temporal, request_id, require_role
from aether.audit.writer import AuditRecord, record
from aether.core.enums import Role
from aether.core.errors import ConflictError, NotFoundError
from aether.core.inventory import ResourceType
from aether.db.models import CloudConnection, Resource, Snapshot
from aether.inventory.store import VmFilter, neighbours, search_resources, summary
from aether.inventory.topology import TopologyNode, network_topology, to_mermaid
from aether.workflows.discovery import DiscoverConnectionWorkflow, DiscoveryInput

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["inventory"])

Viewer = Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))]
Analyst = Annotated[WorkspaceContext, Depends(require_role(Role.ANALYST))]
Temporal = Annotated[Client, Depends(get_temporal)]
RequestId = Annotated[str | None, Depends(request_id)]


class SnapshotOut(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    regions: list[str] | None
    coverage: list[dict[str, Any]] | None
    stats: dict[str, Any] | None
    error: str | None


def _snap(s: Snapshot) -> SnapshotOut:
    return SnapshotOut(
        id=s.id,
        connection_id=s.connection_id,
        provider=s.provider,
        status=s.status,
        started_at=s.started_at,
        finished_at=s.finished_at,
        regions=s.regions,
        coverage=s.coverage,
        stats=s.stats,
        error=s.error,
    )


class ResourceSummary(BaseModel):
    id: uuid.UUID
    type: str
    native_id: str
    name: str | None
    provider: str
    account: str
    region: str
    zone: str | None
    status: str | None
    tags: dict[str, Any]
    vcpu: int | None
    memory_mib: int | None
    cpu_arch: str | None
    os_family: str | None
    source_sku: str | None
    snapshot_id: uuid.UUID
    connection_id: uuid.UUID
    discovered_at: datetime


def _summary(r: Resource) -> ResourceSummary:
    return ResourceSummary(
        id=r.id,
        type=r.type,
        native_id=r.native_id,
        name=r.name,
        provider=r.provider,
        account=r.account,
        region=r.region,
        zone=r.zone,
        status=r.status,
        tags=r.tags,
        vcpu=r.vcpu,
        memory_mib=r.memory_mib,
        cpu_arch=r.cpu_arch,
        os_family=r.os_family,
        source_sku=r.spec.get("source_sku"),
        snapshot_id=r.snapshot_id,
        connection_id=r.connection_id,
        discovered_at=r.discovered_at,
    )


class ResourcePage(BaseModel):
    items: list[ResourceSummary]
    total: int
    limit: int
    offset: int


class Neighbour(BaseModel):
    direction: Literal["in", "out"]
    kind: str
    resource: ResourceSummary


class ResourceDetail(ResourceSummary):
    spec: dict[str, Any]
    raw: dict[str, Any]
    created_at_source: datetime | None
    neighbours: list[Neighbour]
    snapshot: SnapshotOut


# ----------------------------------------------------------------------------- discovery


@router.post(
    "/connections/{connection_id}/discover", response_model=SnapshotOut, status_code=status.HTTP_202_ACCEPTED
)
async def start_discovery(
    connection_id: uuid.UUID, ctx: Analyst, temporal: Temporal, settings: SettingsDep, rid: RequestId
) -> SnapshotOut:
    conn = (
        await ctx.session.execute(
            select(CloudConnection).where(
                CloudConnection.id == connection_id, CloudConnection.workspace_id == ctx.workspace_id
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise NotFoundError("connection not found")
    running = (
        await ctx.session.execute(
            select(Snapshot).where(Snapshot.connection_id == conn.id, Snapshot.status == "running")
        )
    ).scalar_one_or_none()
    if running is not None:
        # A run that never finalised (e.g. worker lost) is abandoned after 2 hours.
        if datetime.now(running.started_at.tzinfo) - running.started_at < timedelta(hours=2):
            raise ConflictError("a discovery run is already in progress for this connection")
        running.status = "failed"
        running.error = "abandoned: did not finish within 2 hours"

    snap = Snapshot(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        connection_id=conn.id,
        provider=conn.provider,
        status="running",
        requested_by=ctx.principal.user_id,
    )
    snap.workflow_id = f"discovery-{snap.id}"
    ctx.session.add(snap)
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="discovery.start",
            workspace_id=ctx.workspace_id,
            connection_id=conn.id,
            target_type="snapshot",
            target_id=str(snap.id),
            details={"connection": conn.name},
            request_id=rid,
        ),
    )
    await ctx.session.flush()
    await ctx.session.refresh(snap)
    out = _snap(snap)
    # The snapshot row must be committed before the workflow's activities look for it.
    await ctx.session.commit()
    await temporal.start_workflow(
        DiscoverConnectionWorkflow.run,
        DiscoveryInput(
            workspace_id=str(ctx.workspace_id),
            connection_id=str(conn.id),
            snapshot_id=str(snap.id),
            requested_by=str(ctx.principal.user_id),
            requested_by_display=ctx.principal.actor.display,
            request_id=rid,
        ),
        id=snap.workflow_id,
        task_queue=settings.connector_task_queue,
        execution_timeout=timedelta(hours=2),
    )
    return out


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    ctx: Viewer,
    connection_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SnapshotOut]:
    q = select(Snapshot).where(Snapshot.workspace_id == ctx.workspace_id)
    if connection_id:
        q = q.where(Snapshot.connection_id == connection_id)
    rows = (await ctx.session.execute(q.order_by(Snapshot.started_at.desc()).limit(limit))).scalars()
    return [_snap(s) for s in rows]


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
async def get_snapshot(snapshot_id: uuid.UUID, ctx: Viewer) -> SnapshotOut:
    s = await ctx.session.get(Snapshot, snapshot_id)
    if s is None or s.workspace_id != ctx.workspace_id:
        raise NotFoundError("snapshot not found")
    return _snap(s)


# ----------------------------------------------------------------------------- inventory


@router.get("/inventory/summary")
async def inventory_summary(ctx: Viewer) -> dict[str, Any]:
    return await summary(ctx.session)


@router.get("/inventory/resources", response_model=ResourcePage)
async def list_resources(
    ctx: Viewer,
    type: ResourceType = ResourceType.VM,
    snapshot_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    provider: str | None = None,
    region: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    os_family: str | None = None,
    cpu_arch: str | None = None,
    min_vcpu: Annotated[int | None, Query(ge=0)] = None,
    min_memory_gib: Annotated[float | None, Query(ge=0)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=256, description="key or key=value")] = None,
    sort: Literal["name", "vcpu", "memory", "region", "status"] = "name",
    order: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResourcePage:
    tag_key, _, tag_value = (tag or "").partition("=")
    f = VmFilter(
        snapshot_id=snapshot_id,
        connection_id=connection_id,
        provider=provider,
        region=region,
        status=status_,
        os_family=os_family,
        cpu_arch=cpu_arch,
        min_vcpu=min_vcpu,
        min_memory_mib=int(min_memory_gib * 1024) if min_memory_gib is not None else None,
        q=q,
        tag_key=tag_key or None,
        tag_value=tag_value if "=" in (tag or "") else None,
    )
    rows, total = await search_resources(
        ctx.session, type, f, limit=limit, offset=offset, sort=sort, descending=order == "desc"
    )
    return ResourcePage(items=[_summary(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/inventory/resources/{resource_id}", response_model=ResourceDetail)
async def get_resource(resource_id: uuid.UUID, ctx: Viewer) -> ResourceDetail:
    r = await ctx.session.get(Resource, resource_id)
    if r is None or r.workspace_id != ctx.workspace_id:
        raise NotFoundError("resource not found")
    snap = await ctx.session.get(Snapshot, r.snapshot_id)
    assert snap is not None
    nb = await neighbours(ctx.session, r)
    return ResourceDetail(
        **_summary(r).model_dump(),
        spec=r.spec,
        raw=r.raw,
        created_at_source=r.created_at_source,
        neighbours=[Neighbour(direction=d, kind=k, resource=_summary(o)) for d, k, o in nb],
        snapshot=_snap(snap),
    )


# ----------------------------------------------------------------------------- topology


class TopologyNodeOut(BaseModel):
    id: uuid.UUID
    type: str
    native_id: str
    name: str | None
    parent: uuid.UUID | None
    status: str | None
    detail: str | None


class TopologyOut(BaseModel):
    network: TopologyNodeOut
    nodes: list[TopologyNodeOut]
    edges: list[dict[str, str]]
    truncated: bool
    mermaid: str


@router.get("/inventory/topology/{network_id}", response_model=TopologyOut)
async def topology(network_id: uuid.UUID, ctx: Viewer, security_groups: bool = True) -> TopologyOut:
    topo = await network_topology(ctx.session, network_id, include_security_groups=security_groups)
    if topo is None:
        raise NotFoundError("network not found")

    def out(n: TopologyNode) -> TopologyNodeOut:
        return TopologyNodeOut(
            id=n.id,
            type=n.type,
            native_id=n.native_id,
            name=n.name,
            parent=n.parent,
            status=n.status,
            detail=n.label_detail,
        )

    return TopologyOut(
        network=out(topo.network),
        nodes=[out(n) for n in topo.nodes],
        edges=topo.edges,
        truncated=topo.truncated,
        mermaid=to_mermaid(topo),
    )
