"""Inventory persistence and queries. All functions expect a workspace-scoped session (RLS)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aether.core.inventory import SCHEMA_VERSION, NormalizedBundle, ResourceType
from aether.db.models import Resource, ResourceEdge, Snapshot

USABLE_SNAPSHOT_STATUSES = ("complete", "partial")
_BATCH = 500


async def write_bundle(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    provider: str,
    bundle: NormalizedBundle,
) -> dict[str, int]:
    """Insert resources and edges. Idempotent (deterministic ids + ON CONFLICT DO NOTHING),
    so a retried activity never duplicates rows."""
    rows: list[dict[str, Any]] = []
    for r in bundle.resources:
        vm = r.spec if r.type == ResourceType.VM else {}
        rows.append(
            {
                "id": r.id,
                "workspace_id": workspace_id,
                "snapshot_id": snapshot_id,
                "connection_id": connection_id,
                "provider": provider,
                "type": r.type.value,
                "native_id": r.native_id,
                "name": r.name,
                "account": r.account,
                "region": r.region,
                "zone": r.zone,
                "status": r.status,
                "tags": r.tags,
                "spec": r.spec,
                "raw": r.raw,
                "schema_version": SCHEMA_VERSION,
                "vcpu": vm.get("vcpu"),
                "memory_mib": vm.get("memory_mib"),
                "cpu_arch": vm.get("cpu_arch"),
                "os_family": vm.get("os_family"),
                "created_at_source": r.created_at_source,
            }
        )
    for i in range(0, len(rows), _BATCH):
        await session.execute(insert(Resource).values(rows[i : i + _BATCH]).on_conflict_do_nothing())

    edges = [
        {
            "snapshot_id": snapshot_id,
            "workspace_id": workspace_id,
            "from_id": e.from_id,
            "to_id": e.to_id,
            "kind": e.kind.value,
        }
        for e in bundle.edges
    ]
    for i in range(0, len(edges), _BATCH):
        await session.execute(insert(ResourceEdge).values(edges[i : i + _BATCH]).on_conflict_do_nothing())

    counts: dict[str, int] = {}
    for r in bundle.resources:
        counts[r.type.value] = counts.get(r.type.value, 0) + 1
    return counts


def latest_snapshot_ids() -> Select[tuple[uuid.UUID]]:
    """Subquery: the most recent usable snapshot of every connection (RLS-scoped)."""
    ranked = (
        select(
            Snapshot.id,
            func.row_number()
            .over(partition_by=Snapshot.connection_id, order_by=Snapshot.started_at.desc())
            .label("rn"),
        )
        .where(Snapshot.status.in_(USABLE_SNAPSHOT_STATUSES))
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.rn == 1)


@dataclass
class VmFilter:
    snapshot_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    provider: str | None = None
    region: str | None = None
    status: str | None = None
    os_family: str | None = None
    cpu_arch: str | None = None
    min_vcpu: int | None = None
    min_memory_mib: int | None = None
    q: str | None = None
    tag_key: str | None = None
    tag_value: str | None = None


SORTABLE = {
    "name": Resource.name,
    "vcpu": Resource.vcpu,
    "memory": Resource.memory_mib,
    "region": Resource.region,
    "status": Resource.status,
}


async def search_resources(
    session: AsyncSession,
    rtype: ResourceType,
    f: VmFilter,
    *,
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    descending: bool = False,
) -> tuple[list[Resource], int]:
    conds = [Resource.type == rtype.value]
    if f.snapshot_id:
        conds.append(Resource.snapshot_id == f.snapshot_id)
    else:
        conds.append(Resource.snapshot_id.in_(latest_snapshot_ids()))
    if f.connection_id:
        conds.append(Resource.connection_id == f.connection_id)
    if f.provider:
        conds.append(Resource.provider == f.provider)
    if f.region:
        conds.append(Resource.region == f.region)
    if f.status:
        conds.append(Resource.status == f.status)
    if f.os_family:
        conds.append(Resource.os_family == f.os_family)
    if f.cpu_arch:
        conds.append(Resource.cpu_arch == f.cpu_arch)
    if f.min_vcpu is not None:
        conds.append(Resource.vcpu >= f.min_vcpu)
    if f.min_memory_mib is not None:
        conds.append(Resource.memory_mib >= f.min_memory_mib)
    if f.q:
        like = f"%{f.q.replace('%', r'\%').replace('_', r'\_')}%"
        conds.append(or_(Resource.name.ilike(like), Resource.native_id.ilike(like)))
    if f.tag_key:
        conds.append(Resource.tags.has_key(f.tag_key))
        if f.tag_value is not None:
            conds.append(Resource.tags[f.tag_key].astext == f.tag_value)

    where = and_(*conds)
    total = (await session.execute(select(func.count()).select_from(Resource).where(where))).scalar_one()
    col = SORTABLE.get(sort, Resource.name)
    order = (col.desc().nulls_last() if descending else col.asc().nulls_last(), Resource.id)
    rows = await session.execute(select(Resource).where(where).order_by(*order).limit(limit).offset(offset))
    return list(rows.scalars()), total


async def neighbours(session: AsyncSession, resource: Resource) -> list[tuple[str, str, Resource]]:
    """(direction, kind, other resource) for every edge touching ``resource``."""
    out: list[tuple[str, str, Resource]] = []
    q_out = (
        select(ResourceEdge.kind, Resource)
        .join(Resource, Resource.id == ResourceEdge.to_id)
        .where(ResourceEdge.from_id == resource.id)
    )
    q_in = (
        select(ResourceEdge.kind, Resource)
        .join(Resource, Resource.id == ResourceEdge.from_id)
        .where(ResourceEdge.to_id == resource.id)
    )
    for kind, other in (await session.execute(q_out)).all():
        out.append(("out", kind, other))
    for kind, other in (await session.execute(q_in)).all():
        out.append(("in", kind, other))
    return out


async def summary(session: AsyncSession) -> dict[str, Any]:
    latest = latest_snapshot_ids()
    type_rows = (
        await session.execute(
            select(Resource.type, func.count())
            .where(Resource.snapshot_id.in_(latest))
            .group_by(Resource.type)
        )
    ).all()
    by_type: dict[str, int] = {t: int(c) for t, c in type_rows}
    by_region = (
        await session.execute(
            select(Resource.region, func.count())
            .where(Resource.snapshot_id.in_(latest), Resource.type == ResourceType.VM.value)
            .group_by(Resource.region)
            .order_by(func.count().desc())
        )
    ).all()
    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(Resource.vcpu), 0), func.coalesce(func.sum(Resource.memory_mib), 0)
            ).where(Resource.snapshot_id.in_(latest), Resource.type == ResourceType.VM.value)
        )
    ).one()
    return {
        "resources_by_type": by_type,
        "vms_by_region": [{"region": r, "count": c} for r, c in by_region],
        "total_vcpu": int(totals[0]),
        "total_memory_mib": int(totals[1]),
    }
