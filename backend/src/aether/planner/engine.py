"""Deterministic migration planner (PROJECT_PLAN §15). Pure: same inputs → same plan.

The AI never writes plan steps. A plan is data: scope, prerequisites, waves of steps
(each with pre-check, action, post-check and compensation), downtime estimate,
rollback and assumptions. Its SHA-256 over canonical JSON is what approvals sign.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field

PLANNER_VERSION = "2026.09.1"


class PlanOptions(BaseModel):
    mechanism: Literal["azure_migrate", "cold_image"] = "azure_migrate"
    replication_bandwidth_mbps: int = Field(500, ge=10, le=100_000)
    cutover_window: str = "Saturday 22:00-02:00 UTC"
    exclude_blocked: bool = True
    resource_group: str = "rg-migration"


class PlanVm(BaseModel):
    resource_id: str
    native_id: str
    name: str | None
    source_sku: str | None
    os_family: str
    os: str | None
    vcpu: int | None
    memory_mib: int | None
    target_sku: str | None
    target_family: str | None
    disks_gib: int
    subnet: str | None
    security_groups: list[str] = Field(default_factory=list)
    load_balancers: list[str] = Field(default_factory=list)
    readiness: str
    open_findings: list[dict[str, Any]] = Field(default_factory=list)  # warnings/blockers not acknowledged
    monthly_usd: dict[str, float | None] = Field(default_factory=dict)
    one_time_usd: float | None = None


class Step(BaseModel):
    id: str
    title: str
    pre_check: str
    action: str
    post_check: str
    compensation: str
    automated: bool = False  # executed by the platform in a later release; manual/tool-driven in v1


class Wave(BaseModel):
    number: int
    name: str
    vms: list[str]  # native ids
    reason: str
    data_gib: int
    initial_sync_hours: float
    cutover_downtime_minutes: int
    steps: list[Step]


class PlanContent(BaseModel):
    planner_version: str = PLANNER_VERSION
    source: dict[str, Any]
    target: dict[str, Any]
    options: PlanOptions
    scope: list[PlanVm]
    excluded: list[dict[str, Any]]
    prerequisites: list[dict[str, Any]]
    waves: list[Wave]
    rollback: list[str]
    totals: dict[str, Any]
    assumptions: list[str]
    inputs: dict[str, Any]  # snapshot ids, assessment run, catalog timestamps


def canonical_hash(content: PlanContent) -> str:
    raw = json.dumps(content.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _waves(vms: list[PlanVm]) -> list[list[PlanVm]]:
    """Machines behind the same load balancer move together (union-find over shared LBs)."""
    parent = {v.native_id: v.native_id for v in vms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_lb: dict[str, list[str]] = defaultdict(list)
    for v in vms:
        for lb in v.load_balancers:
            by_lb[lb].append(v.native_id)
    for members in by_lb.values():
        for m in members[1:]:
            parent[find(m)] = find(members[0])
    groups: dict[str, list[PlanVm]] = defaultdict(list)
    for v in vms:
        groups[find(v.native_id)].append(v)
    # Standalone machines are batched in waves of up to 10; LB pools stay intact.
    pools = [g for g in groups.values() if len(g) > 1 or g[0].load_balancers]
    singles = sorted(
        (g[0] for g in groups.values() if len(g) == 1 and not g[0].load_balancers),
        key=lambda v: (v.readiness != "ready", v.name or v.native_id),
    )
    batches = [singles[i : i + 10] for i in range(0, len(singles), 10)]
    # Low-risk first: standalone ready machines, then pools (which need coordinated cutover).
    ordered = batches + sorted(pools, key=lambda g: (len(g), g[0].name or g[0].native_id))
    return [sorted(g, key=lambda v: v.name or v.native_id) for g in ordered if g]


def _steps(wave_no: int, opts: PlanOptions, has_lb: bool) -> list[Step]:
    p = f"W{wave_no}"
    replicate = (
        Step(
            id=f"{p}.2",
            title="Start replication (Azure Migrate)",
            pre_check="Replication appliance registered; source servers reachable; target subnet and NSGs exist.",
            action="Enable server replication for every machine in the wave to the target resource group and subnet.",
            post_check="Initial replication completed; delta sync healthy for 24 h.",
            compensation="Disable replication and delete replicated disks in the target (source untouched).",
        )
        if opts.mechanism == "azure_migrate"
        else Step(
            id=f"{p}.2",
            title="Snapshot, export and import disk images",
            pre_check="Staging storage account exists; egress budget approved.",
            action="Snapshot source volumes, export as VHD, upload to the staging account, create managed disks.",
            post_check="Managed disks created with expected sizes and checksums.",
            compensation="Delete staged VHDs and managed disks; delete source snapshots.",
        )
    )
    steps = [
        Step(
            id=f"{p}.1",
            title="Pre-flight checks",
            pre_check="Plan approved and within its approval window.",
            action="Verify vCPU quota per family, landing-zone deployment (tofu plan = no changes), connectivity, "
            "and that open findings for these machines are resolved or acknowledged.",
            post_check="All pre-flight checks pass; results attached to the change record.",
            compensation="None required (read-only).",
        ),
        replicate,
        Step(
            id=f"{p}.3",
            title="Test migration in an isolated network",
            pre_check="Isolated test VNet without routes to production.",
            action="Boot test copies; run smoke tests (services up, ports listening, application health endpoints).",
            post_check="Application owners sign off the test.",
            compensation="Clean up test VMs.",
        ),
        Step(
            id=f"{p}.4",
            title="Cutover" + (" (with load balancer switch)" if has_lb else ""),
            pre_check=f"Change window open ({opts.cutover_window}); DNS TTLs lowered ≥24 h earlier; backups verified.",
            action="Stop application writes; stop source VMs; final sync; start target VMs; "
            + ("repoint traffic to the Azure load balancer/Application Gateway; " if has_lb else "")
            + "update DNS.",
            post_check="Health checks green; synthetic transactions succeed; error rates at baseline.",
            compensation="Rollback: repoint DNS/traffic to the source and start the source VMs (unchanged).",
        ),
        Step(
            id=f"{p}.5",
            title="Hypercare and decommission hand-off",
            pre_check="Cutover completed.",
            action="Monitor for 7 days; keep the source stopped (not deleted). Decommission is a separate, manual change.",
            post_check="No regressions for 7 days; owners approve decommissioning.",
            compensation="Rollback remains possible while the source exists.",
        ),
    ]
    return steps


def build_plan(
    vms: list[PlanVm],
    excluded: list[dict[str, Any]],
    *,
    source: dict[str, Any],
    target: dict[str, Any],
    options: PlanOptions,
    networks: list[dict[str, Any]],
    inputs: dict[str, Any],
) -> PlanContent:
    scope = sorted(vms, key=lambda v: v.name or v.native_id)
    waves: list[Wave] = []
    for i, group in enumerate(_waves(scope), start=1):
        data = sum(v.disks_gib for v in group)
        hours = round(data * 8 * 1024 / options.replication_bandwidth_mbps / 3600, 1)
        has_lb = any(v.load_balancers for v in group)
        downtime = (
            30 + (15 if has_lb else 0)
            if options.mechanism == "azure_migrate"
            else max(30, math.ceil(data * 8 * 1024 / options.replication_bandwidth_mbps / 60) + 20)
        )
        lbs = sorted({lb for v in group for lb in v.load_balancers})
        waves.append(
            Wave(
                number=i,
                name=f"Wave {i}" + (f": {', '.join(lbs)}" if lbs else ""),
                vms=[v.native_id for v in group],
                reason=(
                    "machines share load balancer(s) and must cut over together"
                    if has_lb
                    else "independent machines, ordered by readiness"
                ),
                data_gib=data,
                initial_sync_hours=hours,
                cutover_downtime_minutes=downtime,
                steps=_steps(i, options, has_lb),
            )
        )

    quota: dict[str, int] = defaultdict(int)
    for v in scope:
        if v.target_family and v.vcpu:
            quota[v.target_family] += v.vcpu
    identities = [v.native_id for v in scope if any(f["rule_id"] == "ID-001" for f in v.open_findings)]
    prerequisites: list[dict[str, Any]] = [
        {
            "id": "PRE-1",
            "title": "Landing zone",
            "detail": f"Deploy the generated OpenTofu module: resource group {options.resource_group}, "
            f"{len(networks)} VNet(s) mirroring source address spaces, subnets, NSGs and ASGs.",
            "evidence": {"networks": [n["name"] for n in networks]},
        },
        {
            "id": "PRE-2",
            "title": "vCPU quota",
            "detail": f"Request regional quota in {target['region']} per VM family before wave 1.",
            "evidence": {"per_family_vcpu": dict(sorted(quota.items()))},
        },
        {
            "id": "PRE-3",
            "title": "Connectivity",
            "detail": "Site-to-site VPN or private interconnect between source VPCs and target VNets for replication "
            "and cross-cloud dependencies during migration; plan non-overlapping ranges if both sides "
            "must route to each other.",
            "evidence": {"source_cidrs": sorted({c for n in networks for c in n.get("cidrs", [])})},
        },
    ]
    if options.mechanism == "azure_migrate":
        prerequisites.append(
            {
                "id": "PRE-4",
                "title": "Azure Migrate project",
                "detail": "Create an Azure Migrate project and register the replication appliance "
                "(physical-server path for AWS sources).",
                "evidence": {},
            }
        )
    if identities:
        prerequisites.append(
            {
                "id": "PRE-5",
                "title": "Workload identities",
                "detail": "Create managed identities replacing AWS instance roles and grant equivalent "
                "permissions.",
                "evidence": {"machines": identities},
            }
        )
    open_blockers = [
        f"{v.native_id}:{f['rule_id']}" for v in scope for f in v.open_findings if f["severity"] == "blocker"
    ]

    def tsum(key: str) -> float | None:
        vals = [v.monthly_usd.get(key) for v in scope]
        return None if not vals or any(x is None for x in vals) else round(sum(x or 0 for x in vals), 2)

    totals = {
        "vms": len(scope),
        "waves": len(waves),
        "data_gib": sum(v.disks_gib for v in scope),
        "target_monthly_usd": {k: tsum(k) for k in ("on_demand", "reserved_1y", "reserved_3y")},
        "one_time_usd": round(sum(v.one_time_usd or 0 for v in scope), 2),
        "open_blockers": open_blockers,
        "open_warnings": sum(1 for v in scope for f in v.open_findings if f["severity"] == "warning"),
    }
    assumptions = [
        f"initial replication time = data size / {options.replication_bandwidth_mbps} Mbps sustained throughput",
        "cutover downtime covers final sync, boot and DNS/traffic switch; application-level checks are extra",
        "source machines are never modified or deleted by this plan; rollback is always to the untouched source",
        "costs are list-price estimates frozen at plan creation (see inputs.catalog)",
        f"target region {target['region']}; target sizes from the assessment run's sizing strategy",
    ]
    rollback = [
        "Trigger: failed post-cutover health checks, data inconsistency, or owner decision within hypercare.",
        "Repoint DNS / load balancer traffic back to the source endpoints (TTL already lowered).",
        "Start the source VMs (stopped, unchanged since cutover); confirm health.",
        "Reverse-sync any data written on the target during the failed window if the application requires it.",
        "Leave the target resources stopped for analysis; record the rollback in the change and audit logs.",
    ]
    return PlanContent(
        source=source,
        target=target,
        options=options,
        scope=scope,
        excluded=excluded,
        prerequisites=prerequisites,
        waves=waves,
        rollback=rollback,
        totals=totals,
        assumptions=assumptions,
        inputs=inputs,
    )


def to_markdown(name: str, version: int, content_hash: str, plan: PlanContent) -> str:
    t = plan.totals
    lines = [
        f"# Migration plan: {name} (v{version})",
        "",
        f"- **Target:** {plan.target['provider']} {plan.target['region']}  ",
        f"- **Mechanism:** {plan.options.mechanism.replace('_', ' ')}  ",
        f"- **Scope:** {t['vms']} machines, {t['data_gib']} GiB, {t['waves']} waves  ",
        f"- **Content hash:** `{content_hash}`",
        "",
        "## Prerequisites",
        "",
    ]
    for p in plan.prerequisites:
        lines.append(f"- **{p['id']} {p['title']}:** {p['detail']}")
    lines += ["", "## Waves", ""]
    for w in plan.waves:
        lines += [
            f"### {w.name}",
            "",
            f"{w.reason}. Data {w.data_gib} GiB, initial sync ≈ {w.initial_sync_hours} h, "
            f"cutover downtime ≈ {w.cutover_downtime_minutes} min.",
            "",
            "Machines: " + ", ".join(w.vms),
            "",
        ]
        lines += ["| Step | Pre-check | Action | Post-check | Compensation |", "|---|---|---|---|---|"]
        for s in w.steps:
            lines.append(
                f"| {s.id} {s.title} | {s.pre_check} | {s.action} | {s.post_check} | {s.compensation} |"
            )
        lines.append("")
    lines += [
        "## Scope",
        "",
        "| Machine | Source | Target | Readiness | Open findings |",
        "|---|---|---|---|---|",
    ]
    for v in plan.scope:
        findings = ", ".join(f["rule_id"] for f in v.open_findings) or "—"
        lines.append(
            f"| {v.name or v.native_id} | {v.source_sku} | {v.target_sku} | {v.readiness} | {findings} |"
        )
    if plan.excluded:
        lines += ["", "## Excluded (blocked)", ""] + [
            f"- {e['native_id']}: {e['reason']}" for e in plan.excluded
        ]
    lines += ["", "## Rollback", ""] + [f"1. {r}" for r in plan.rollback]
    lines += ["", "## Assumptions", ""] + [f"- {a}" for a in plan.assumptions]
    return "\n".join(lines) + "\n"
