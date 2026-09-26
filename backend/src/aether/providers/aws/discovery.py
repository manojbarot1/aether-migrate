"""Collect raw AWS inventory for one region (network I/O only; no normalisation).

Each resource family is fetched independently, so a missing permission or a throttled
API degrades coverage for that family instead of failing the whole region. Coverage is
reported per family and shown to users. Partial data is never presented as complete.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from aether.core.inventory import CoverageEntry, CoverageStatus
from aether.providers.aws.adapter import BOTO_CONFIG
from aether.providers.aws.normalize import RegionRaw

_DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "UnauthorizedException"}
_THROTTLED = {"Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException"}
_DISABLED = {"OptInRequired", "AuthFailure"}  # AuthFailure: region not enabled for the account


def _classify(e: Exception) -> tuple[CoverageStatus, str]:
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "ClientError")
        if code in _DENIED:
            return CoverageStatus.DENIED, code
        if code in _THROTTLED:
            return CoverageStatus.THROTTLED_PARTIAL, code
        if code in _DISABLED:
            return CoverageStatus.DISABLED, code
        return CoverageStatus.ERROR, code
    return CoverageStatus.ERROR, type(e).__name__


def _paginate(client: Any, op: str, key: str, **kwargs: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in client.get_paginator(op).paginate(**kwargs):
        out.extend(page.get(key) or [])
    return out


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def list_enabled_regions(session: boto3.session.Session) -> list[str]:
    regions = session.client("ec2", config=BOTO_CONFIG).describe_regions()["Regions"]
    return sorted(r["RegionName"] for r in regions)


def collect_region(
    session: boto3.session.Session,
    account: str,
    region: str,
    heartbeat: Callable[[str], None] = lambda _: None,
) -> tuple[RegionRaw, list[CoverageEntry]]:
    ec2 = session.client("ec2", region_name=region, config=BOTO_CONFIG)
    elb = session.client("elbv2", region_name=region, config=BOTO_CONFIG)
    raw = RegionRaw(account=account, region=region)
    coverage: list[CoverageEntry] = []

    def family(kind: str, fn: Callable[[], None]) -> bool:
        heartbeat(f"{region}:{kind}")
        try:
            fn()
        except (ClientError, BotoCoreError) as e:
            status, detail = _classify(e)
            coverage.append(CoverageEntry(region=region, kind=kind, status=status, detail=detail))
            return False
        coverage.append(CoverageEntry(region=region, kind=kind, status=CoverageStatus.OK))
        return True

    def instances() -> None:
        for res in _paginate(ec2, "describe_instances", "Reservations"):
            for inst in res.get("Instances") or []:
                if (inst.get("State") or {}).get("Name") != "terminated":
                    raw.instances.append(inst)

    def instance_types() -> None:
        types = sorted({i["InstanceType"] for i in raw.instances if i.get("InstanceType")})
        for chunk in _chunks(types, 100):
            for t in _paginate(ec2, "describe_instance_types", "InstanceTypes", InstanceTypes=chunk):
                raw.instance_types[t["InstanceType"]] = t

    def images() -> None:
        ids = sorted({i["ImageId"] for i in raw.instances if i.get("ImageId")})
        for chunk in _chunks(ids, 100):
            # Deregistered/shared-then-revoked AMIs are simply absent from the answer.
            for img in ec2.describe_images(ImageIds=chunk, IncludeDeprecated=True).get("Images") or []:
                raw.images[img["ImageId"]] = dict(img)

    def volumes() -> None:
        raw.volumes = _paginate(ec2, "describe_volumes", "Volumes")

    def enis() -> None:
        raw.network_interfaces = _paginate(ec2, "describe_network_interfaces", "NetworkInterfaces")

    def subnets() -> None:
        raw.subnets = _paginate(ec2, "describe_subnets", "Subnets")

    def vpcs() -> None:
        raw.vpcs = _paginate(ec2, "describe_vpcs", "Vpcs")

    def sgs() -> None:
        raw.security_groups = _paginate(ec2, "describe_security_groups", "SecurityGroups")

    def load_balancers() -> None:
        raw.load_balancers = _paginate(elb, "describe_load_balancers", "LoadBalancers")
        for lb in raw.load_balancers:
            arn = lb["LoadBalancerArn"]
            for tg in _paginate(elb, "describe_target_groups", "TargetGroups", LoadBalancerArn=arn):
                if tg.get("TargetType") != "instance":
                    continue
                health = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                raw.lb_targets.setdefault(arn, []).extend(
                    d["Target"]["Id"] for d in health.get("TargetHealthDescriptions") or []
                )

    if family("ec2:instances", instances):
        # Specs and images only make sense once instances are known.
        family("ec2:instance_types", instance_types)
        family("ec2:images", images)
    family("ec2:volumes", volumes)
    family("ec2:network_interfaces", enis)
    family("ec2:subnets", subnets)
    family("ec2:vpcs", vpcs)
    family("ec2:security_groups", sgs)
    family("elbv2:load_balancers", load_balancers)
    return raw, coverage
