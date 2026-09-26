"""Built-in tools. Each one calls the same endpoint function the REST API uses.

Model projections are deliberately compact (tool results are referenced by id, and
the full data goes to the UI card) and never include connection configuration,
raw provider payloads or secrets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from aether.api.deps import WorkspaceContext
from aether.api.routers import assessments as assessments_api
from aether.api.routers import compare as compare_api
from aether.api.routers import connections as connections_api
from aether.api.routers import inventory as inventory_api
from aether.api.routers import plans as plans_api
from aether.catalog.store import priced_regions
from aether.core.enums import Role
from aether.core.errors import AetherError, NotFoundError, ValidationFailedError
from aether.core.inventory import ResourceType
from aether.cost.engine import CostOptions
from aether.db.models import Resource
from aether.inventory.store import summary as inventory_summary_q
from aether.planner.engine import PlanOptions
from aether.sizing.engine import Strategy
from aether.tools.registry import Card, SideEffect, ToolContext, ToolOutput, tool

MODEL_ROWS = 25  # rows given to the model; the card shows everything returned


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _link(ctx: ToolContext, path: str) -> str:
    return f"/w/{ctx.workspace_id}/{path}"


def _gib(mib: int | None) -> float | None:
    return round(mib / 1024, 1) if mib is not None else None


def _age_hours(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    return round((datetime.now(UTC) - ts).total_seconds() / 3600, 1)


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if ts else None


def _unavailable_temporal(ctx: ToolContext) -> Any:
    if ctx.temporal is None:
        raise AetherError("the workflow engine is unavailable; try again shortly")
    return ctx.temporal


# ============================================================================ connections


class NoArgs(_In):
    pass


@tool(
    "connections_list",
    title="List cloud connections",
    description=(
        "List the workspace's cloud connections (metadata only: name, provider, status, last test). "
        "Use it to find a connection id before checking discovery status or starting a rescan."
    ),
    input_model=NoArgs,
    min_role=Role.VIEWER,
)
async def connections_list(ctx: ToolContext, w: WorkspaceContext, _: NoArgs) -> ToolOutput:
    rows = await connections_api.list_connections(w)
    items = [
        {
            "id": str(c.id),
            "name": c.name,
            "provider": c.provider.value,
            "mode": c.mode.value,
            "status": c.status,
            "last_tested_at": _iso(c.last_tested_at),
        }
        for c in rows
    ]
    return ToolOutput(
        model={"connections": items, "count": len(items)},
        card=Card(
            kind="connections",
            title="Cloud connections",
            data={"items": items},
            link=_link(ctx, "connections"),
        ),
    )


# ============================================================================ discovery


class DiscoveryStatusIn(_In):
    connection_id: uuid.UUID | None = Field(None, description="limit to one connection")


@tool(
    "discovery_status",
    title="Discovery status",
    description=(
        "Freshness of the inventory: the latest discovery run per connection with its status, finish time, "
        "age in hours, region coverage and resource counts. Use it before answering questions that depend on "
        "how current the inventory is."
    ),
    input_model=DiscoveryStatusIn,
    min_role=Role.VIEWER,
)
async def discovery_status(ctx: ToolContext, w: WorkspaceContext, args: DiscoveryStatusIn) -> ToolOutput:
    conns = {c.id: c for c in await connections_api.list_connections(w)}
    snaps = await inventory_api.list_snapshots(w, connection_id=args.connection_id, limit=100)
    latest: dict[uuid.UUID, Any] = {}
    last_ok: dict[uuid.UUID, Any] = {}
    for s in snaps:  # newest first
        latest.setdefault(s.connection_id, s)
        if s.status in ("complete", "partial"):
            last_ok.setdefault(s.connection_id, s)
    items = []
    for cid, c in conns.items():
        if args.connection_id and cid != args.connection_id:
            continue
        run = latest.get(cid)
        ok = last_ok.get(cid)
        coverage = (ok.coverage if ok else None) or []
        failed = [c_ for c_ in coverage if c_.get("status") != "ok"]
        items.append(
            {
                "connection_id": str(cid),
                "connection": c.name,
                "latest_run": {"status": run.status, "started_at": _iso(run.started_at)} if run else None,
                "inventory_as_of": _iso(ok.finished_at) if ok else None,
                "age_hours": _age_hours(ok.finished_at) if ok else None,
                "regions": len(ok.regions or []) if ok else 0,
                "coverage_gaps": len(failed),
                "stats": (ok.stats or {}) if ok else {},
            }
        )
    return ToolOutput(
        model={"connections": items},
        card=Card(
            kind="discovery_status",
            title="Inventory freshness",
            data={"items": items},
            link=_link(ctx, "discovery"),
        ),
    )


class DiscoveryRefreshIn(_In):
    connection_id: uuid.UUID


@tool(
    "discovery_refresh",
    title="Start a discovery run",
    description=(
        "Start a new read-only discovery run for one connection (it only reads from the cloud). "
        "Only call this when the user explicitly asks to rescan or refresh. Returns immediately; "
        "the run continues in the background."
    ),
    input_model=DiscoveryRefreshIn,
    min_role=Role.ANALYST,
    side_effect=SideEffect.READ_WORKFLOW,
)
async def discovery_refresh(ctx: ToolContext, w: WorkspaceContext, args: DiscoveryRefreshIn) -> ToolOutput:
    snap = await inventory_api.start_discovery(
        args.connection_id, w, _unavailable_temporal(ctx), ctx.settings, ctx.request_id
    )
    data = {"snapshot_id": str(snap.id), "connection_id": str(snap.connection_id), "status": snap.status}
    return ToolOutput(
        model={**data, "note": "discovery started; it runs in the background"},
        card=Card(
            kind="discovery_started", title="Discovery started", data=data, link=_link(ctx, "discovery")
        ),
    )


# ============================================================================ inventory


@tool(
    "inventory_summary",
    title="Inventory summary",
    description=(
        "Totals for the latest inventory: resource counts by type, VMs by region, OS, CPU architecture and "
        "status, and total vCPU and memory."
    ),
    input_model=NoArgs,
    min_role=Role.VIEWER,
)
async def inventory_summary(ctx: ToolContext, w: WorkspaceContext, _: NoArgs) -> ToolOutput:
    s = await inventory_summary_q(w.session)
    return ToolOutput(
        model=s,
        card=Card(kind="inventory_summary", title="Inventory summary", data=s, link=_link(ctx, "inventory")),
    )


class SearchVmsIn(_In):
    q: str | None = Field(None, max_length=200, description="text match on name, native id or IP")
    provider: Literal["aws", "azure", "gcp", "ibm"] | None = None
    region: str | None = Field(None, max_length=64, description="source region, e.g. eu-central-1")
    status: str | None = Field(None, max_length=32, description="e.g. running, stopped")
    os_family: Literal["linux", "windows", "unknown"] | None = None
    cpu_arch: Literal["x86_64", "arm64"] | None = None
    min_vcpu: int | None = Field(None, ge=0, le=1024)
    min_memory_gib: float | None = Field(None, ge=0, le=65536)
    tag: str | None = Field(None, max_length=256, description="tag key, or key=value")
    sort: Literal["name", "vcpu", "memory", "region", "status"] = "name"
    order: Literal["asc", "desc"] = "asc"
    limit: int = Field(50, ge=1, le=200)


def _vm_row(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "native_id": r.native_id,
        "region": r.region,
        "status": r.status,
        "os_family": r.os_family,
        "vcpu": r.vcpu,
        "memory_gib": _gib(r.memory_mib),
        "cpu_arch": r.cpu_arch,
        "source_sku": r.source_sku,
        "tags": dict(list(r.tags.items())[:10]),
    }


@tool(
    "inventory_search_vms",
    title="Search virtual machines",
    description=(
        "Search discovered virtual machines in the latest inventory with filters (text, region, status, OS, "
        "architecture, minimum vCPU/memory, tag). Returns resource ids that other tools accept."
    ),
    input_model=SearchVmsIn,
    min_role=Role.VIEWER,
)
async def inventory_search_vms(ctx: ToolContext, w: WorkspaceContext, a: SearchVmsIn) -> ToolOutput:
    page = await inventory_api.list_resources(
        w,
        type=ResourceType.VM,
        snapshot_id=None,
        connection_id=None,
        provider=a.provider,
        region=a.region,
        status_=a.status,
        os_family=a.os_family,
        cpu_arch=a.cpu_arch,
        min_vcpu=a.min_vcpu,
        min_memory_gib=a.min_memory_gib,
        q=a.q,
        tag=a.tag,
        sort=a.sort,
        order=a.order,
        limit=a.limit,
        offset=0,
    )
    rows = [_vm_row(r) for r in page.items]
    as_of = max((r.discovered_at for r in page.items), default=None)
    filters = a.model_dump(exclude_none=True, exclude={"sort", "order", "limit"})
    model: dict[str, Any] = {
        "total_matching": page.total,
        "returned": len(rows),
        "inventory_as_of": _iso(as_of),
        "vms": rows[:MODEL_ROWS],
    }
    if len(rows) > MODEL_ROWS:
        model["note"] = (
            f"only the first {MODEL_ROWS} rows are shown here; the user sees all {len(rows)} in the table"
        )
    query = "&".join(
        f"{k}={v}"
        for k, v in filters.items()
        if k in ("q", "region", "os_family", "cpu_arch", "status", "min_vcpu", "min_memory_gib")
    )
    return ToolOutput(
        model=model,
        card=Card(
            kind="vm_table",
            title=f"{page.total} virtual machine{'s' if page.total != 1 else ''}",
            data={"total": page.total, "items": rows, "filters": filters, "as_of": _iso(as_of)},
            link=_link(ctx, "inventory" + (f"?{query}" if query else "")),
        ),
    )


class GetResourceIn(_In):
    resource_id: uuid.UUID


@tool(
    "inventory_get_resource",
    title="Resource details",
    description=(
        "Details of one discovered resource by id: specification (CPU, memory, disks, network interfaces, OS, "
        "boot mode), tags and its graph neighbours (subnet, security groups, load balancers)."
    ),
    input_model=GetResourceIn,
    min_role=Role.VIEWER,
)
async def inventory_get_resource(ctx: ToolContext, w: WorkspaceContext, a: GetResourceIn) -> ToolOutput:
    d = await inventory_api.get_resource(a.resource_id, w)
    spec = d.spec
    disks = [
        {k: disk.get(k) for k in ("device", "size_gib", "type_class", "iops", "encrypted", "ephemeral")}
        for disk in spec.get("disks") or []
    ]
    nics = [
        {
            k: nic.get(k)
            for k in ("private_ips", "public_ips", "subnet_native_id", "security_group_native_ids")
        }
        for nic in spec.get("nics") or []
    ]
    model: dict[str, Any] = {
        "id": str(d.id),
        "type": d.type,
        "name": d.name,
        "native_id": d.native_id,
        "provider": d.provider,
        "account": d.account,
        "region": d.region,
        "zone": d.zone,
        "status": d.status,
        "tags": d.tags,
        "discovered_at": _iso(d.discovered_at),
    }
    if d.type == ResourceType.VM.value:
        model |= {
            "vcpu": d.vcpu,
            "memory_gib": _gib(d.memory_mib),
            "cpu_arch": d.cpu_arch,
            "source_sku": spec.get("source_sku"),
            "os": {k: spec.get(k) for k in ("os_family", "os_distribution", "os_version", "license_model")},
            "boot_mode": spec.get("boot_mode"),
            "disks": disks,
            "nics": nics,
        }
    elif d.type in (ResourceType.NETWORK.value, ResourceType.SUBNET.value):
        model["spec"] = {k: spec.get(k) for k in ("cidrs", "cidr", "network_native_id", "is_default")}
    elif d.type == ResourceType.SECURITY_GROUP.value:
        model["rules"] = (spec.get("rules") or [])[:40]
    model["neighbours"] = [
        {
            "direction": n.direction,
            "kind": n.kind,
            "type": n.resource.type,
            "id": str(n.resource.id),
            "name": n.resource.name,
        }
        for n in d.neighbours[:40]
    ]
    return ToolOutput(
        model=model,
        card=Card(
            kind="resource", title=d.name or d.native_id, data=model, link=_link(ctx, f"inventory/{d.id}")
        ),
    )


# ============================================================================ topology


class TopologyIn(_In):
    network_id: uuid.UUID | None = Field(None, description="id of a VPC/virtual network resource")
    resource_id: uuid.UUID | None = Field(None, description="or: any VM or subnet in that network")


async def _network_for(w: WorkspaceContext, rid: uuid.UUID) -> uuid.UUID:
    r = await w.session.get(Resource, rid)
    if r is None or r.workspace_id != w.workspace_id:
        raise NotFoundError("resource not found")
    if r.type == ResourceType.NETWORK.value:
        return r.id
    net_native: str | None = None
    if r.type == ResourceType.SUBNET.value:
        net_native = r.spec.get("network_native_id")
    elif r.type == ResourceType.VM.value:
        subnet = next(
            (n.get("subnet_native_id") for n in r.spec.get("nics") or [] if n.get("subnet_native_id")), None
        )
        if subnet:
            s = (
                await w.session.execute(
                    select(Resource).where(
                        Resource.snapshot_id == r.snapshot_id,
                        Resource.type == ResourceType.SUBNET.value,
                        Resource.native_id == subnet,
                    )
                )
            ).scalar_one_or_none()
            net_native = s.spec.get("network_native_id") if s else None
    if not net_native:
        raise ValidationFailedError("this resource is not attached to a known network")
    net = (
        await w.session.execute(
            select(Resource.id).where(
                Resource.snapshot_id == r.snapshot_id,
                Resource.type == ResourceType.NETWORK.value,
                Resource.native_id == net_native,
            )
        )
    ).scalar_one_or_none()
    if net is None:
        raise NotFoundError("network not found in the same snapshot")
    return net


@tool(
    "topology_get",
    title="Network topology",
    description=(
        "Topology of one network: subnets, VMs, load balancer routes and security-group references. "
        "Pass a network id, or any VM or subnet id to use the network it belongs to."
    ),
    input_model=TopologyIn,
    min_role=Role.VIEWER,
)
async def topology_get(ctx: ToolContext, w: WorkspaceContext, a: TopologyIn) -> ToolOutput:
    if not a.network_id and not a.resource_id:
        raise ValidationFailedError("give network_id or resource_id")
    net_id = a.network_id or await _network_for(w, a.resource_id)  # type: ignore[arg-type]
    t = await inventory_api.topology(net_id, w, security_groups=True)
    counts: dict[str, int] = {}
    for n in t.nodes:
        counts[n.type] = counts.get(n.type, 0) + 1
    model = {
        "network": {
            "id": str(t.network.id),
            "name": t.network.name,
            "native_id": t.network.native_id,
            "cidr": t.network.detail,
        },
        "counts": counts,
        "edges": len(t.edges),
        "truncated": t.truncated,
        "subnets": [
            {"id": str(n.id), "name": n.name, "cidr": n.detail}
            for n in t.nodes
            if n.type == ResourceType.SUBNET.value
        ][:30],
    }
    return ToolOutput(
        model=model,
        card=Card(
            kind="topology",
            title=f"Topology of {t.network.name or t.network.native_id}",
            data={**model, "mermaid": t.mermaid},
            link=_link(ctx, f"topology?network={t.network.id}"),
        ),
    )


# ============================================================================ catalog, sizing and cost


@tool(
    "catalog_regions",
    title="Priced target regions",
    description=(
        "Azure regions that have prices in the catalog, with the age of the oldest price. Use it to pick a "
        "valid target_region for cost comparisons, assessments and plans."
    ),
    input_model=NoArgs,
    min_role=Role.VIEWER,
)
async def catalog_regions(ctx: ToolContext, w: WorkspaceContext, _: NoArgs) -> ToolOutput:
    regions = [
        {"region": r["region"], "prices": r["prices"], "price_age_hours": _age_hours(r["oldest_price_at"])}
        for r in await priced_regions(w.session, "azure")
    ]
    return ToolOutput(model={"azure_regions": regions}, card=None)


class CompareIn(_In):
    resource_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    target_region: str = Field(max_length=64, description="Azure region, e.g. westeurope")
    strategy: Strategy = Strategy.LIKE_FOR_LIKE
    currency: str = Field("EUR", pattern=r"^[A-Za-z]{3}$")


@tool(
    "cost_compare",
    title="Compare target size and cost",
    description=(
        "Recommend Azure sizes for the given VMs and estimate monthly source vs target cost (on-demand, 1-year "
        "and 3-year reserved) plus one-time migration cost. Strategies: like_for_like, right_sized, "
        "cheapest_fit. Amounts are in USD with the requested display currency's exchange rate."
    ),
    input_model=CompareIn,
    min_role=Role.ANALYST,
)
async def cost_compare(ctx: ToolContext, w: WorkspaceContext, a: CompareIn) -> ToolOutput:
    res = await compare_api.compare(
        compare_api.CompareRequest(
            resource_ids=a.resource_ids,
            target_region=a.target_region,
            strategy=a.strategy,
            options=CostOptions(currency=a.currency.upper()),
        ),
        w,
        ctx.request_id,
    )
    rows = []
    for i in res.items:
        top = i.sizing.candidates[0] if i.sizing.candidates else None
        rows.append(
            {
                "id": str(i.resource_id),
                "name": i.name,
                "source_sku": i.source_sku,
                "target_sku": top.sku if top else None,
                "no_fit_reason": None if top else "; ".join(i.sizing.warnings) or "no size fits",
                "source_monthly_usd": i.cost.source.total_monthly_usd.get("on_demand") if i.cost else None,
                "target_monthly_usd": i.cost.target.total_monthly_usd if i.cost else None,
            }
        )
    model: dict[str, Any] = {
        "target": f"azure/{res.target_region}",
        "strategy": res.strategy.value,
        "currency": res.currency,
        "fx_per_usd": res.fx_per_usd,
        "fx_date": res.fx_date,
        "target_prices_as_of": _iso(res.target_prices_as_of),
        "target_prices_stale": res.target_price_stale,
        "source_prices_available": res.source_prices_available,
        "totals_usd": res.totals,
        "vms": rows[:MODEL_ROWS],
    }
    return ToolOutput(
        model=model,
        card=Card(
            kind="cost_compare",
            title=f"Cost in Azure {res.target_region} ({len(rows)} VMs)",
            data={**model, "vms": rows},
            link=_link(ctx, "compare"),
        ),
    )


# ============================================================================ assessment


class AssessIn(_In):
    resource_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    target_region: str = Field(max_length=64, description="Azure region, e.g. westeurope")
    strategy: Strategy = Strategy.LIKE_FOR_LIKE


def _assessment_model(run: Any) -> dict[str, Any]:
    items = run.items or []
    return {
        "run_id": str(run.id),
        "created_at": _iso(run.created_at),
        "target": f"{run.target_provider}/{run.target_region}",
        "strategy": run.strategy,
        "ruleset_version": run.ruleset_version,
        "summary": run.summary,
        "vms": [
            {
                "id": i["resource_id"],
                "name": i.get("name"),
                "readiness": i["readiness"],
                "score": i.get("score"),
                "target_sku": i.get("target_sku"),
                "blockers": [f["rule_id"] for f in i["findings"] if f["severity"] == "blocker"],
                "warnings": [
                    f["rule_id"]
                    for f in i["findings"]
                    if f["severity"] == "warning" and not f.get("acknowledged")
                ],
            }
            for i in items[:MODEL_ROWS]
        ],
    }


@tool(
    "assessment_run",
    title="Run a readiness assessment",
    description=(
        "Assess migration readiness of the given VMs for an Azure region: rule findings (blockers, warnings), "
        "readiness and score per VM, proposed size and quota needs. Creates an assessment run record that "
        "plan_create can use."
    ),
    input_model=AssessIn,
    min_role=Role.ANALYST,
    side_effect=SideEffect.DRAFT,
)
async def assessment_run(ctx: ToolContext, w: WorkspaceContext, a: AssessIn) -> ToolOutput:
    run = await assessments_api.run_assessment(
        assessments_api.AssessRequest(
            resource_ids=a.resource_ids, target_region=a.target_region, strategy=a.strategy
        ),
        w,
        ctx.request_id,
    )
    model = _assessment_model(run)
    return ToolOutput(
        model=model,
        card=Card(
            kind="assessment",
            title=f"Readiness for Azure {run.target_region}",
            data=model,
            link=_link(ctx, f"assessment/{run.id}"),
        ),
    )


class AssessmentGetIn(_In):
    run_id: uuid.UUID | None = Field(None, description="omit for the most recent run")


@tool(
    "assessment_get",
    title="Get an assessment run",
    description="Summary and per-VM readiness of an assessment run (the most recent one if no id is given).",
    input_model=AssessmentGetIn,
    min_role=Role.VIEWER,
)
async def assessment_get(ctx: ToolContext, w: WorkspaceContext, a: AssessmentGetIn) -> ToolOutput:
    if a.run_id is None:
        runs = await assessments_api.list_assessments(w, limit=1)
        if not runs:
            raise NotFoundError("no assessment has been run in this workspace yet")
        run_id = runs[0].id
    else:
        run_id = a.run_id
    run = await assessments_api.get_assessment(run_id, w)
    model = _assessment_model(run)
    return ToolOutput(
        model=model,
        card=Card(
            kind="assessment",
            title=f"Readiness for Azure {run.target_region}",
            data=model,
            link=_link(ctx, f"assessment/{run.id}"),
        ),
    )


# ============================================================================ plans


class PlanCreateIn(_In):
    assessment_run_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    exclude_blocked: bool = True
    mechanism: Literal["azure_migrate", "cold_image"] = "azure_migrate"
    cutover_window: str = Field("Saturday 22:00-02:00 UTC", max_length=100)


def _plan_model(p: Any) -> dict[str, Any]:
    c = p.content
    return {
        "plan_id": str(p.id),
        "name": p.name,
        "version": p.version,
        "status": p.status,
        "content_hash": p.content_hash[:12],
        "totals": c.get("totals", {}),
        "waves": [
            {
                "number": wv["number"],
                "name": wv["name"],
                "vms": len(wv["vms"]),
                "reason": wv["reason"],
                "cutover_downtime_minutes": wv["cutover_downtime_minutes"],
                "initial_sync_hours": wv["initial_sync_hours"],
            }
            for wv in c.get("waves", [])
        ],
        "excluded": c.get("excluded", [])[:MODEL_ROWS],
        "prerequisites": [x.get("title") for x in c.get("prerequisites", [])],
        "assumptions": c.get("assumptions", []),
    }


@tool(
    "plan_create",
    title="Create a draft migration plan",
    description=(
        "Create a DRAFT migration plan from an assessment run: waves, steps, downtime estimates, costs and an "
        "OpenTofu landing zone. Drafts change nothing in any cloud and need a separate human review. Only call "
        "this when the user asks for a plan."
    ),
    input_model=PlanCreateIn,
    min_role=Role.ANALYST,
    side_effect=SideEffect.DRAFT,
)
async def plan_create(ctx: ToolContext, w: WorkspaceContext, a: PlanCreateIn) -> ToolOutput:
    p = await plans_api.create_plan(
        plans_api.CreatePlan(
            name=a.name,
            assessment_run_id=a.assessment_run_id,
            options=PlanOptions(
                exclude_blocked=a.exclude_blocked, mechanism=a.mechanism, cutover_window=a.cutover_window
            ),
        ),
        w,
        ctx.request_id,
    )
    model = _plan_model(p)
    return ToolOutput(
        model=model, card=Card(kind="plan", title=p.name, data=model, link=_link(ctx, f"plans/{p.id}"))
    )


class PlanGetIn(_In):
    plan_id: uuid.UUID


@tool(
    "plan_get",
    title="Get a migration plan",
    description="A plan's status, waves, downtime, totals, exclusions, prerequisites and assumptions.",
    input_model=PlanGetIn,
    min_role=Role.VIEWER,
)
async def plan_get(ctx: ToolContext, w: WorkspaceContext, a: PlanGetIn) -> ToolOutput:
    p = await plans_api.get_plan(a.plan_id, w)
    model = _plan_model(p) | {
        "reviews": [{"decision": r.decision, "at": _iso(r.created_at)} for r in p.reviews]
    }
    return ToolOutput(
        model=model, card=Card(kind="plan", title=p.name, data=model, link=_link(ctx, f"plans/{p.id}"))
    )


@tool(
    "plan_list",
    title="List migration plans",
    description="Migration plans in the workspace (newest first) with status, version and totals.",
    input_model=NoArgs,
    min_role=Role.VIEWER,
)
async def plan_list(ctx: ToolContext, w: WorkspaceContext, _: NoArgs) -> ToolOutput:
    plans = await plans_api.list_plans(w)
    items = [
        {
            "plan_id": str(p.id),
            "name": p.name,
            "version": p.version,
            "status": p.status,
            "created_at": _iso(p.created_at),
            "vms": p.totals.get("vms"),
            "waves": p.totals.get("waves"),
        }
        for p in plans[:50]
    ]
    return ToolOutput(
        model={"plans": items[:MODEL_ROWS], "count": len(plans)},
        card=Card(kind="plan_list", title="Migration plans", data={"items": items}, link=_link(ctx, "plans")),
    )
