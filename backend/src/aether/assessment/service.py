"""Builds assessment contexts from the inventory graph and runs the rules (DB-backed)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.assessment.engine import Context, VmAssessment, assess
from aether.catalog.store import RegionCatalog
from aether.core.inventory import EdgeKind, ResourceType, VmSpec
from aether.db.models import FindingAcknowledgement, Resource, ResourceEdge
from aether.sizing.engine import SizingResult, Strategy, recommend


async def _neighbourhood(
    session: AsyncSession, vm: Resource
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sg_rows = (
        (
            await session.execute(
                select(Resource)
                .join(ResourceEdge, ResourceEdge.to_id == Resource.id)
                .where(
                    ResourceEdge.from_id == vm.id,
                    ResourceEdge.kind == EdgeKind.PROTECTED_BY.value,
                    Resource.type == ResourceType.SECURITY_GROUP.value,
                )
            )
        )
        .scalars()
        .all()
    )
    lb_rows = (
        (
            await session.execute(
                select(Resource)
                .join(ResourceEdge, ResourceEdge.from_id == Resource.id)
                .where(
                    ResourceEdge.to_id == vm.id,
                    ResourceEdge.kind == EdgeKind.ROUTES_TO.value,
                    Resource.type == ResourceType.LOAD_BALANCER.value,
                )
            )
        )
        .scalars()
        .all()
    )
    sgs = [{"native_id": s.native_id, "name": s.name, "rules": s.spec.get("rules") or []} for s in sg_rows]
    lbs = [
        {
            "native_id": lb.native_id,
            "name": lb.name,
            "kind": lb.spec.get("kind"),
            "scheme": lb.spec.get("scheme"),
        }
        for lb in lb_rows
    ]
    return sgs, lbs


async def acknowledgements(
    session: AsyncSession, native_ids: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    rows = (
        (
            await session.execute(
                select(FindingAcknowledgement).where(FindingAcknowledgement.native_id.in_(native_ids))
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for a in rows:
        out.setdefault(a.native_id, {})[a.rule_id] = {
            "reason": a.reason,
            "by": a.acknowledged_by_display,
            "at": a.acknowledged_at.isoformat(),
        }
    return out


async def assess_vms(
    session: AsyncSession, vms: list[Resource], target: RegionCatalog, strategy: Strategy
) -> tuple[list[VmAssessment], list[tuple[str, SizingResult | None]]]:
    acks = await acknowledgements(session, [v.native_id for v in vms])
    results: list[VmAssessment] = []
    sizings: list[tuple[str, SizingResult | None]] = []
    for r in sorted(vms, key=lambda x: x.name or x.native_id):
        spec = VmSpec.model_validate(r.spec)
        sizing = recommend(spec, target.specs, target.hourly, strategy) if target.specs else None
        sgs, lbs = await _neighbourhood(session, r)
        ctx = Context(
            vm=spec,
            native_id=r.native_id,
            name=r.name,
            source_provider=r.provider,
            target_provider=target.provider,
            target_region=target.region,
            sizing=sizing,
            tags=r.tags,
            security_groups=sgs,
            load_balancers=lbs,
        )
        results.append(assess(ctx, str(r.id), acks.get(r.native_id)))
        sizings.append((str(r.id), sizing))
    return results, sizings


def run_id() -> uuid.UUID:
    return uuid.uuid4()
