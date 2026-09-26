"""Deterministic target sizing (PROJECT_PLAN §13.2). Pure functions, no I/O, no LLM.

Hard constraints first (architecture, GPU, memory/vCPU floor, local-disk needs, a price
must exist in the target region), then strategy-specific ranking. Every candidate
carries the reasoning that selected it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aether.core.catalog import InstanceSpec, PriceModel
from aether.core.inventory import VmSpec


class Strategy(StrEnum):
    LIKE_FOR_LIKE = "like_for_like"
    RIGHT_SIZED = "right_sized"
    CHEAPEST_FIT = "cheapest_fit"


class Requirement(BaseModel):
    vcpu: int
    memory_mib: int
    cpu_arch: str
    gpu_count: int = 0
    local_disk_gib: int = 0
    basis: str  # "allocation" | "utilization"
    notes: list[str] = Field(default_factory=list)


class SizedCandidate(BaseModel):
    sku: str
    family: str
    vcpu: int
    memory_mib: int
    cpu_arch: str
    local_disk_gib: int
    hourly_usd: float
    spec_source: str
    reasons: list[str]


class SizingResult(BaseModel):
    strategy: Strategy
    requirement: Requirement
    candidates: list[SizedCandidate]
    warnings: list[str] = Field(default_factory=list)


def requirement(
    vm: VmSpec, strategy: Strategy, *, headroom: float = 0.3, utilization: dict[str, float] | None = None
) -> Requirement:
    notes: list[str] = []
    vcpu = vm.vcpu or 1
    mem = vm.memory_mib or 1024
    arch = vm.cpu_arch or "x86_64"
    if vm.cpu_arch is None:
        notes.append("source architecture unknown; assumed x86_64")
    basis = "allocation"
    if strategy == Strategy.RIGHT_SIZED:
        cpu_p95 = (utilization or {}).get("cpu_p95")
        mem_p95 = (utilization or {}).get("mem_p95")
        if cpu_p95 is not None:
            vcpu = max(1, math.ceil(vcpu * cpu_p95 * (1 + headroom)))
            basis = "utilization"
            notes.append(f"vCPU from p95 CPU {cpu_p95:.0%} + {headroom:.0%} headroom")
        else:
            notes.append("no CPU utilisation metrics; vCPU sized on allocation")
        if mem_p95 is not None:
            mem = max(1024, math.ceil(mem * mem_p95 * (1 + headroom)))
            basis = "utilization"
            notes.append(f"memory from p95 usage {mem_p95:.0%} + {headroom:.0%} headroom")
        else:
            notes.append("no memory metrics (needs an in-guest agent); memory sized on allocation")
    local = sum(d.size_gib or 0 for d in vm.disks if d.ephemeral)
    if local:
        notes.append(
            f"source has {local} GiB of instance-store (ephemeral) disk; prefer SKUs with local temp disk"
        )
    return Requirement(
        vcpu=vcpu,
        memory_mib=mem,
        cpu_arch=arch,
        gpu_count=vm.gpu_count,
        local_disk_gib=local,
        basis=basis,
        notes=notes,
    )


def recommend(
    vm: VmSpec,
    specs: Sequence[InstanceSpec],
    hourly_price: Any,  # callable (sku, os, model) -> float | None
    strategy: Strategy = Strategy.LIKE_FOR_LIKE,
    *,
    limit: int = 3,
    headroom: float = 0.3,
    utilization: dict[str, float] | None = None,
) -> SizingResult:
    req = requirement(vm, strategy, headroom=headroom, utilization=utilization)
    os_ = "windows" if vm.os_family == "windows" else "linux"
    warnings: list[str] = []

    eligible: list[tuple[InstanceSpec, float]] = []
    rejected = {"arch": 0, "size": 0, "gpu": 0, "price": 0}
    for s in specs:
        if s.cpu_arch != req.cpu_arch:
            rejected["arch"] += 1
            continue
        if s.vcpu < req.vcpu or s.memory_mib < req.memory_mib:
            rejected["size"] += 1
            continue
        if s.gpu_count < req.gpu_count:
            rejected["gpu"] += 1
            continue
        price = hourly_price(s.sku, os_, PriceModel.ON_DEMAND)
        if price is None:
            rejected["price"] += 1
            continue
        eligible.append((s, price))

    if req.local_disk_gib:
        with_local = [(s, p) for s, p in eligible if s.local_disk_gib >= req.local_disk_gib]
        if with_local:
            eligible = with_local
        else:
            warnings.append(
                "no eligible SKU has enough local temp disk; ephemeral data must move to managed disks"
            )

    if not eligible:
        # Report the constraint that actually eliminated the last viable sizes.
        same_arch = [s for s in specs if s.cpu_arch == req.cpu_arch]
        big_enough = [s for s in same_arch if s.vcpu >= req.vcpu and s.memory_mib >= req.memory_mib]
        with_gpu = [s for s in big_enough if s.gpu_count >= req.gpu_count]
        reason = "no eligible target SKU"
        if not same_arch:
            reason += f": the target catalog has no {req.cpu_arch} sizes"
        elif not big_enough:
            reason += f": nothing with ≥{req.vcpu} vCPU and ≥{req.memory_mib / 1024:g} GiB"
        elif not with_gpu:
            reason += f": nothing with ≥{req.gpu_count} GPU(s)"
        else:
            reason += f": {len(with_gpu)} matching size(s) have no price in this region (catalog not synced?)"
        return SizingResult(strategy=strategy, requirement=req, candidates=[], warnings=[*warnings, reason])

    if strategy == Strategy.LIKE_FOR_LIKE:
        eligible.sort(
            key=lambda sp: (
                sp[0].vcpu - req.vcpu,
                sp[0].memory_mib - req.memory_mib,
                sp[1],
                -sp[0].generation,
            )
        )
    else:
        eligible.sort(key=lambda sp: (sp[1], sp[0].vcpu, sp[0].memory_mib, -sp[0].generation))

    out: list[SizedCandidate] = []
    for s, price in eligible[:limit]:
        reasons = [
            f"{s.vcpu} vCPU / {s.memory_mib // 1024} GiB ≥ required {req.vcpu} vCPU / "
            f"{req.memory_mib / 1024:g} GiB ({req.basis})",
            f"{s.cpu_arch} ({s.cpu_vendor or 'unknown vendor'}), generation {s.generation}",
            f"${price:.4f}/h on demand ({os_})",
        ]
        if s.vcpu > req.vcpu * 2 or s.memory_mib > req.memory_mib * 2:
            reasons.append("more than double the required size: consider right-sizing with utilisation data")
        if s.local_disk_gib:
            reasons.append(f"{s.local_disk_gib} GiB local temp disk")
        out.append(
            SizedCandidate(
                sku=s.sku,
                family=s.family,
                vcpu=s.vcpu,
                memory_mib=s.memory_mib,
                cpu_arch=s.cpu_arch,
                local_disk_gib=s.local_disk_gib,
                hourly_usd=price,
                spec_source=s.spec_source,
                reasons=reasons,
            )
        )
    if any(c.spec_source == "curated" for c in out):
        warnings.append(
            "target size specs come from the curated catalog; verify against the provider before ordering"
        )
    return SizingResult(strategy=strategy, requirement=req, candidates=out, warnings=warnings)
