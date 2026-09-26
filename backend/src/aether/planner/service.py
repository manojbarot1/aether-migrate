"""Assembles planner inputs from an assessment run and the inventory graph (DB-backed)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.catalog.store import load_region
from aether.core.errors import NotFoundError, ValidationFailedError
from aether.core.inventory import EdgeKind, ResourceType, VmSpec
from aether.cost.engine import CostOptions, estimate
from aether.db.models import AssessmentRun, Resource, ResourceEdge
from aether.iac import azure as azure_iac
from aether.planner.engine import PlanContent, PlanOptions, PlanVm, build_plan, canonical_hash


def _os_label(spec: VmSpec) -> str | None:
    if not spec.os_distribution:
        return None if spec.os_family == "unknown" else spec.os_family
    return f"{spec.os_distribution} {spec.os_version or ''}".strip()


async def generate(
    session: AsyncSession, run_id: uuid.UUID, name: str, options: PlanOptions
) -> tuple[PlanContent, str, dict[str, Any]]:
    run = await session.get(AssessmentRun, run_id)
    if run is None:
        raise NotFoundError("assessment run not found")
    items = {i["resource_id"]: i for i in run.items}
    rows = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.id.in_([uuid.UUID(k) for k in items]), Resource.type == ResourceType.VM.value
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise ValidationFailedError("the assessed machines are no longer in the inventory")

    target = await load_region(session, run.target_provider, run.target_region)
    src_cache: dict[str, Any] = {}

    in_scope: list[PlanVm] = []
    excluded: list[dict[str, Any]] = []
    for r in rows:
        item = items[str(r.id)]
        spec = VmSpec.model_validate(r.spec)
        blockers = [f for f in item["findings"] if f["severity"] == "blocker"]
        if options.exclude_blocked and blockers:
            excluded.append(
                {
                    "native_id": r.native_id,
                    "name": r.name,
                    "reason": "blocked by " + ", ".join(sorted({f["rule_id"] for f in blockers})),
                }
            )
            continue
        lbs = (
            await session.execute(
                select(Resource.name, Resource.native_id)
                .join(ResourceEdge, ResourceEdge.from_id == Resource.id)
                .where(ResourceEdge.to_id == r.id, ResourceEdge.kind == EdgeKind.ROUTES_TO.value)
            )
        ).all()
        if r.region not in src_cache:
            src_cache[r.region] = await load_region(session, r.provider, r.region)
        cost = estimate(
            spec,
            item.get("target_sku"),
            target.hourly,
            target.disks,
            src_cache[r.region].hourly,
            src_cache[r.region].disks,
            CostOptions(),
        )
        in_scope.append(
            PlanVm(
                resource_id=str(r.id),
                native_id=r.native_id,
                name=r.name,
                source_sku=spec.source_sku,
                os_family=spec.os_family,
                os=_os_label(spec),
                vcpu=spec.vcpu,
                memory_mib=spec.memory_mib,
                target_sku=item.get("target_sku"),
                target_family=item.get("target_family"),
                disks_gib=sum(d.size_gib or 0 for d in spec.disks if not d.ephemeral),
                subnet=next((n.subnet_native_id for n in spec.nics if n.subnet_native_id), None),
                security_groups=sorted({sg for n in spec.nics for sg in n.security_group_native_ids}),
                load_balancers=sorted(name or nid for name, nid in lbs),
                readiness=item["readiness"],
                open_findings=[
                    {"rule_id": f["rule_id"], "severity": f["severity"], "title": f["title"]}
                    for f in item["findings"]
                    if f["severity"] != "info" and not f["acknowledged"]
                ],
                monthly_usd=cost.target.total_monthly_usd,
                one_time_usd=round(cost.one_time.egress_usd + (cost.one_time.dual_running_usd or 0), 2),
            )
        )
    if not in_scope:
        raise ValidationFailedError(
            "every assessed machine is blocked; resolve blockers or include them explicitly"
        )

    # Landing-zone inputs from the same snapshots: VPCs, their subnets, and relevant security groups.
    snapshots = {r.snapshot_id for r in rows}
    subnet_ids = {v.subnet for v in in_scope if v.subnet}
    net_rows = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.snapshot_id.in_(snapshots),
                    Resource.type == ResourceType.SUBNET.value,
                    Resource.native_id.in_(subnet_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    vpc_ids = {s.spec.get("network_native_id") for s in net_rows}
    networks = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.snapshot_id.in_(snapshots),
                    Resource.type == ResourceType.NETWORK.value,
                    Resource.native_id.in_(vpc_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    all_subnets = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.snapshot_id.in_(snapshots), Resource.type == ResourceType.SUBNET.value
                )
            )
        )
        .scalars()
        .all()
    )
    sg_rows = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.snapshot_id.in_(snapshots), Resource.type == ResourceType.SECURITY_GROUP.value
                )
            )
        )
        .scalars()
        .all()
    )
    wanted = {sg for v in in_scope for sg in v.security_groups}
    by_id = {g.native_id: g for g in sg_rows}
    for sg in list(wanted):  # include groups referenced by in-scope groups so ASGs can be created
        for rule in (by_id[sg].spec.get("rules") or []) if sg in by_id else []:
            if rule.get("peer_group_native_id") in by_id:
                wanted.add(rule["peer_group_native_id"])

    net_data = [
        {"native_id": n.native_id, "name": n.name or n.native_id, "cidrs": n.spec.get("cidrs") or []}
        for n in sorted(networks, key=lambda x: x.native_id)
    ]
    subnet_data = [
        {
            "native_id": s.native_id,
            "name": s.name or s.native_id,
            "cidr": s.spec.get("cidr"),
            "network_native_id": s.spec.get("network_native_id"),
        }
        for s in sorted(all_subnets, key=lambda x: x.native_id)
        if s.spec.get("network_native_id") in vpc_ids
    ]
    sg_data = [
        {"native_id": g.native_id, "name": g.name or g.native_id, "rules": g.spec.get("rules") or []}
        for g in sorted(sg_rows, key=lambda x: x.native_id)
        if g.native_id in wanted
    ]

    content = build_plan(
        in_scope,
        sorted(excluded, key=lambda e: e["native_id"]),
        source={
            "provider": rows[0].provider,
            "accounts": sorted({r.account for r in rows}),
            "regions": sorted({r.region for r in rows}),
        },
        target={"provider": run.target_provider, "region": run.target_region, "strategy": run.strategy},
        options=options,
        networks=net_data,
        inputs={
            "assessment_run_id": str(run.id),
            "ruleset_version": run.ruleset_version,
            "snapshot_ids": sorted(str(s) for s in snapshots),
            "catalog": {
                "target_prices_as_of": target.prices_fetched_at.isoformat()
                if target.prices_fetched_at
                else None,
                "sources": sorted(target.price_sources),
            },
        },
    )
    files, notes = azure_iac.render(
        plan_name=name,
        region=run.target_region,
        resource_group=options.resource_group,
        networks=net_data,
        subnets=subnet_data,
        security_groups=sg_data,
        vm_bindings=[
            {
                "native_id": v.native_id,
                "subnet": v.subnet,
                "security_groups": v.security_groups,
                "target_sku": v.target_sku,
            }
            for v in content.scope
        ],
    )
    return content, canonical_hash(content), {"format": "opentofu", "files": files, "notes": notes}
