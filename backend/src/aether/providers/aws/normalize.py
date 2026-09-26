"""Pure normalisation of AWS describe-API output into the neutral inventory model.

No network or database access: the input is exactly what the EC2/ELBv2 APIs return
(collected by :mod:`aether.providers.aws.discovery`), so this module is tested against
recorded fixtures and moto.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aether.core.inventory import (
    DiskSpec,
    EdgeKind,
    LoadBalancerSpec,
    NetworkSpec,
    NicSpec,
    NormalizedBundle,
    NormalizedEdge,
    NormalizedResource,
    Provenance,
    ResourceType,
    SecurityGroupSpec,
    SecurityRule,
    SubnetSpec,
    VmSpec,
    resource_id,
)

Raw = dict[str, Any]


@dataclass
class RegionRaw:
    account: str
    region: str
    instances: list[Raw] = field(default_factory=list)
    instance_types: dict[str, Raw] = field(default_factory=dict)
    images: dict[str, Raw] = field(default_factory=dict)
    volumes: list[Raw] = field(default_factory=list)
    network_interfaces: list[Raw] = field(default_factory=list)
    subnets: list[Raw] = field(default_factory=list)
    vpcs: list[Raw] = field(default_factory=list)
    security_groups: list[Raw] = field(default_factory=list)
    load_balancers: list[Raw] = field(default_factory=list)
    # load balancer ARN -> instance ids registered in any of its target groups
    lb_targets: dict[str, list[str]] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers


def json_safe(obj: Any) -> Any:
    """Round-trip through JSON so datetimes etc. become strings (boto returns datetimes)."""
    return json.loads(json.dumps(obj, default=str))


def tags_of(item: Raw) -> dict[str, str]:
    # aws:* tags are system-managed and noisy; keep them out of the user-facing tag set.
    return {t["Key"]: t.get("Value", "") for t in item.get("Tags") or [] if not t["Key"].startswith("aws:")}


def _name(item: Raw, fallback: str) -> str:
    return tags_of(item).get("Name") or fallback


_STATUS = {
    "pending": "starting",
    "running": "running",
    "stopping": "stopping",
    "stopped": "stopped",
    "shutting-down": "terminating",
    "terminated": "terminated",
}

_OS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ubuntu", re.I), "ubuntu"),
    (re.compile(r"\b(rhel|red ?hat)", re.I), "rhel"),
    (re.compile(r"\bsles|suse", re.I), "sles"),
    (re.compile(r"al2023|amzn2|amazon ?linux|amzn-ami", re.I), "amazon-linux"),
    (re.compile(r"debian", re.I), "debian"),
    (re.compile(r"centos", re.I), "centos"),
    (re.compile(r"rocky", re.I), "rocky"),
    (re.compile(r"alma", re.I), "almalinux"),
    (re.compile(r"oracle ?linux|\bol\d", re.I), "oracle-linux"),
    (re.compile(r"windows", re.I), "windows-server"),
]

_UBUNTU_CODENAMES = {
    "xenial": "16.04",
    "bionic": "18.04",
    "focal": "20.04",
    "jammy": "22.04",
    "noble": "24.04",
    "oracular": "24.10",
    "plucky": "25.04",
    "questing": "25.10",
}


def infer_os(instance: Raw, image: Raw | None) -> dict[str, Any]:
    """Best-effort OS detection. ``PlatformDetails`` is authoritative for the family and
    licence; distribution/version are inferred from the AMI name/description."""
    details = instance.get("PlatformDetails") or (image or {}).get("PlatformDetails") or ""
    platform = (instance.get("Platform") or "").lower()
    out: dict[str, Any] = {"platform_details": details or None, "provenance": {}}

    if platform == "windows" or "windows" in details.lower():
        out["os_family"] = "windows"
    elif details:
        out["os_family"] = "linux"
    else:
        out["os_family"] = "unknown"
    if out["os_family"] != "unknown":
        out["provenance"]["os_family"] = Provenance.DISCOVERED

    # Licence-included AMIs (billing product codes surface in PlatformDetails).
    licensed = ("windows", "red hat", "suse", "sql server", "ubuntu pro")
    if any(k in details.lower() for k in licensed):
        out["license_model"] = "included"
    elif details.lower().startswith("linux/unix"):
        out["license_model"] = "none"
    else:
        out["license_model"] = "unknown"

    text = " ".join(filter(None, [(image or {}).get("Name"), (image or {}).get("Description"), details]))
    for pattern, distro in _OS_PATTERNS:
        if pattern.search(text):
            out["os_distribution"] = distro
            out["provenance"]["os_distribution"] = Provenance.INFERRED
            break
    if out["os_family"] == "unknown" and out.get("os_distribution"):
        out["os_family"] = "windows" if out["os_distribution"] == "windows-server" else "linux"
        out["provenance"]["os_family"] = Provenance.INFERRED
    version = None
    if out.get("os_distribution") == "ubuntu":
        for codename, ver in _UBUNTU_CODENAMES.items():
            if codename in text.lower():
                version = ver
                break
        version = version or _first(re.search(r"ubuntu[^0-9]*(\d{2}\.\d{2})", text, re.I))
    elif out.get("os_distribution") == "windows-server":
        version = _first(re.search(r"(20\d\d)(?:[-_ ]R2)?", text, re.I))
        if version and re.search(rf"{version}[-_ ]R2", text, re.I):
            version += " R2"
    elif out.get("os_distribution") == "amazon-linux":
        version = (
            "2023" if re.search(r"al2023", text, re.I) else "2" if re.search(r"amzn2", text, re.I) else None
        )
    elif out.get("os_distribution") in _VERSION_ANCHORS:
        # Anchor on the distribution keyword so "x86_64" or dates are never read as versions.
        anchor = _VERSION_ANCHORS[out["os_distribution"]]
        version = _first(
            re.search(
                rf"(?:{anchor})[\s_\-]*(?:linux[\s_\-]*)?(?:server[\s_\-]*)?v?(\d{{1,2}}(?:\.\d{{1,2}})?)",
                text,
                re.I,
            )
        )
    if version:
        out["os_version"] = version
        out["provenance"]["os_version"] = Provenance.INFERRED
    return out


_VERSION_ANCHORS = {
    "rhel": r"rhel|red ?hat enterprise",
    "sles": r"sles|suse(?: linux enterprise)?",
    "debian": r"debian",
    "centos": r"centos",
    "rocky": r"rocky",
    "almalinux": r"alma(?:linux)?",
    "oracle-linux": r"oracle|\bol",
}


def _first(m: re.Match[str] | None) -> str | None:
    return m.group(1) if m else None


def _boot_mode(instance: Raw, image: Raw | None) -> str:
    mode = (
        instance.get("CurrentInstanceBootMode") or instance.get("BootMode") or (image or {}).get("BootMode")
    )
    return {"legacy-bios": "bios", "uefi": "uefi", "uefi-preferred": "uefi_preferred"}.get(
        mode or "", "unknown"
    )


def _arch(value: str | None) -> str | None:
    if not value:
        return None
    return (
        "arm64" if value.startswith("arm64") else "x86_64" if value.startswith(("x86_64", "i386")) else None
    )


def _port_range(perm: Raw) -> tuple[int | None, int | None]:
    proto = str(perm.get("IpProtocol", "-1"))
    if proto in ("-1", "all"):
        return None, None
    return perm.get("FromPort"), perm.get("ToPort")


def _rules(sg: Raw) -> list[SecurityRule]:
    rules: list[SecurityRule] = []
    for direction, key in (("ingress", "IpPermissions"), ("egress", "IpPermissionsEgress")):
        for perm in sg.get(key) or []:
            proto = str(perm.get("IpProtocol", "-1"))
            proto = "all" if proto == "-1" else proto
            pf, pt = _port_range(perm)
            for r in perm.get("IpRanges") or []:
                rules.append(
                    SecurityRule(
                        direction=direction,
                        protocol=proto,
                        port_from=pf,
                        port_to=pt,
                        peer_cidr=r.get("CidrIp"),
                        description=r.get("Description"),
                    )
                )
            for r in perm.get("Ipv6Ranges") or []:
                rules.append(
                    SecurityRule(
                        direction=direction,
                        protocol=proto,
                        port_from=pf,
                        port_to=pt,
                        peer_cidr=r.get("CidrIpv6"),
                        description=r.get("Description"),
                    )
                )
            for r in perm.get("UserIdGroupPairs") or []:
                rules.append(
                    SecurityRule(
                        direction=direction,
                        protocol=proto,
                        port_from=pf,
                        port_to=pt,
                        peer_group_native_id=r.get("GroupId"),
                        description=r.get("Description"),
                    )
                )
            for r in perm.get("PrefixListIds") or []:
                rules.append(
                    SecurityRule(
                        direction=direction,
                        protocol=proto,
                        port_from=pf,
                        port_to=pt,
                        peer_prefix_list=r.get("PrefixListId"),
                        description=r.get("Description"),
                    )
                )
    return rules


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- normaliser


def normalize_region(snapshot_id: uuid.UUID, raw: RegionRaw) -> NormalizedBundle:
    bundle = NormalizedBundle()

    def rid(native: str) -> uuid.UUID:
        return resource_id(snapshot_id, native)

    known: set[str] = set()

    def add(rtype: ResourceType, native: str, name: str | None, spec: Any, item: Raw, **kw: Any) -> None:
        known.add(native)
        bundle.resources.append(
            NormalizedResource(
                id=rid(native),
                type=rtype,
                native_id=native,
                name=name,
                account=raw.account,
                region=raw.region,
                tags=tags_of(item),
                spec=spec.model_dump(mode="json", exclude_none=True),
                raw=json_safe(item),
                **kw,
            )
        )

    def edge(a: str, b: str, kind: EdgeKind) -> None:
        bundle.edges.append(NormalizedEdge(from_id=rid(a), to_id=rid(b), kind=kind))

    for vpc in raw.vpcs:
        cidrs = [a["CidrBlock"] for a in vpc.get("CidrBlockAssociationSet") or [] if a.get("CidrBlock")]
        add(
            ResourceType.NETWORK,
            vpc["VpcId"],
            _name(vpc, vpc["VpcId"]),
            NetworkSpec(
                cidrs=cidrs or [vpc.get("CidrBlock")] if vpc.get("CidrBlock") else cidrs,
                is_default=bool(vpc.get("IsDefault")),
            ),
            vpc,
            status=vpc.get("State"),
        )

    for sn in raw.subnets:
        add(
            ResourceType.SUBNET,
            sn["SubnetId"],
            _name(sn, sn["SubnetId"]),
            SubnetSpec(
                cidr=sn.get("CidrBlock"),
                network_native_id=sn.get("VpcId"),
                public_ip_on_launch=sn.get("MapPublicIpOnLaunch"),
            ),
            sn,
            zone=sn.get("AvailabilityZone"),
            status=sn.get("State"),
        )

    for sg in raw.security_groups:
        add(
            ResourceType.SECURITY_GROUP,
            sg["GroupId"],
            sg.get("GroupName") or sg["GroupId"],
            SecurityGroupSpec(
                network_native_id=sg.get("VpcId"), description=sg.get("Description"), rules=_rules(sg)
            ),
            sg,
        )

    volumes = {v["VolumeId"]: v for v in raw.volumes}
    for vol in raw.volumes:
        attach: Raw = next(iter(vol.get("Attachments") or []), {})
        add(
            ResourceType.DISK,
            vol["VolumeId"],
            _name(vol, vol["VolumeId"]),
            DiskSpec(
                native_id=vol["VolumeId"],
                device=attach.get("Device"),
                size_gib=vol.get("Size"),
                type_class=vol.get("VolumeType"),
                iops=vol.get("Iops"),
                throughput_mbps=vol.get("Throughput"),
                encrypted=bool(vol.get("Encrypted")),
                kms_key_ref=vol.get("KmsKeyId"),
                delete_on_termination=attach.get("DeleteOnTermination"),
            ),
            vol,
            zone=vol.get("AvailabilityZone"),
            status=vol.get("State"),
            created_at_source=_dt(vol.get("CreateTime")),
        )

    for eni in raw.network_interfaces:
        add(
            ResourceType.NIC,
            eni["NetworkInterfaceId"],
            eni.get("Description") or eni["NetworkInterfaceId"],
            _nic(eni),
            eni,
            zone=eni.get("AvailabilityZone"),
            status=eni.get("Status"),
        )

    for inst in raw.instances:
        iid = inst["InstanceId"]
        itype = raw.instance_types.get(inst.get("InstanceType", ""), {})
        image = raw.images.get(inst.get("ImageId", ""))
        os_info = infer_os(inst, image)
        provenance = {
            "vcpu": Provenance.DISCOVERED,
            "memory_mib": Provenance.DISCOVERED,
            **os_info.pop("provenance"),
        }

        root = inst.get("RootDeviceName")
        disks: list[DiskSpec] = []
        for m in inst.get("BlockDeviceMappings") or []:
            ebs = m.get("Ebs") or {}
            vol = volumes.get(ebs.get("VolumeId", ""), {})
            disks.append(
                DiskSpec(
                    native_id=ebs.get("VolumeId"),
                    device=m.get("DeviceName"),
                    size_gib=vol.get("Size"),
                    type_class=vol.get("VolumeType"),
                    iops=vol.get("Iops"),
                    throughput_mbps=vol.get("Throughput"),
                    boot=m.get("DeviceName") == root,
                    encrypted=bool(vol.get("Encrypted")),
                    kms_key_ref=vol.get("KmsKeyId"),
                    delete_on_termination=ebs.get("DeleteOnTermination"),
                )
            )
        storage = itype.get("InstanceStorageInfo") or {}
        if storage.get("TotalSizeInGB"):
            disks.append(
                DiskSpec(
                    device="instance-store",
                    size_gib=storage["TotalSizeInGB"],
                    type_class=f"instance-store-{(storage.get('Disks') or [{}])[0].get('Type', 'local')}",
                    ephemeral=True,
                    boot=inst.get("RootDeviceType") == "instance-store",
                )
            )

        gpus = (itype.get("GpuInfo") or {}).get("Gpus") or []
        lifecycle = {"spot": "spot", "scheduled": "scheduled"}.get(
            inst.get("InstanceLifecycle", ""), "on_demand"
        )
        spec = VmSpec(
            source_sku=inst.get("InstanceType"),
            vcpu=(itype.get("VCpuInfo") or {}).get("DefaultVCpus")
            or (inst.get("CpuOptions") or {}).get("CoreCount"),
            memory_mib=(itype.get("MemoryInfo") or {}).get("SizeInMiB"),
            cpu_arch=_arch(inst.get("Architecture")),
            cpu_vendor=((itype.get("ProcessorInfo") or {}).get("Manufacturer")),
            gpu_model=f"{gpus[0].get('Manufacturer', '')} {gpus[0].get('Name', '')}".strip()
            if gpus
            else None,
            gpu_count=sum(g.get("Count", 0) for g in gpus),
            hypervisor=itype.get("Hypervisor") or inst.get("Hypervisor"),
            tenancy=(inst.get("Placement") or {}).get("Tenancy"),
            lifecycle=lifecycle,
            network_performance=(itype.get("NetworkInfo") or {}).get("NetworkPerformance"),
            local_storage_gib=storage.get("TotalSizeInGB"),
            boot_mode=_boot_mode(inst, image),
            image_ref=inst.get("ImageId"),
            image_name=(image or {}).get("Name"),
            disks=disks,
            nics=[_nic(n) for n in inst.get("NetworkInterfaces") or []],
            instance_identity=(inst.get("IamInstanceProfile") or {}).get("Arn"),
            launched_at=_dt(inst.get("LaunchTime")),
            provenance=provenance,
            **os_info,
        )
        if spec.memory_mib is None:
            provenance.pop("memory_mib")
        add(
            ResourceType.VM,
            iid,
            _name(inst, iid),
            spec,
            inst,
            zone=(inst.get("Placement") or {}).get("AvailabilityZone"),
            status=_STATUS.get((inst.get("State") or {}).get("Name", ""), "unknown"),
            created_at_source=_dt(inst.get("LaunchTime")),
        )

    # ---- edges (only between resources present in this bundle)
    for sn in raw.subnets:
        if sn.get("VpcId") in known:
            edge(sn["SubnetId"], sn["VpcId"], EdgeKind.IN_NETWORK)
    for sg in raw.security_groups:
        if sg.get("VpcId") in known:
            edge(sg["GroupId"], sg["VpcId"], EdgeKind.IN_NETWORK)
        peers = {r.peer_group_native_id for r in _rules(sg) if r.peer_group_native_id}
        for peer in sorted(peers - {sg["GroupId"]}):
            if peer in known:
                edge(sg["GroupId"], peer, EdgeKind.REFERENCES)
    for vol in raw.volumes:
        for att in vol.get("Attachments") or []:
            if att.get("InstanceId") in known:
                edge(vol["VolumeId"], att["InstanceId"], EdgeKind.ATTACHED_TO)
    for eni in raw.network_interfaces:
        inst_id = (eni.get("Attachment") or {}).get("InstanceId")
        if inst_id in known:
            edge(eni["NetworkInterfaceId"], inst_id, EdgeKind.ATTACHED_TO)
    for inst in raw.instances:
        iid = inst["InstanceId"]
        if inst.get("SubnetId") in known:
            edge(iid, inst["SubnetId"], EdgeKind.IN_SUBNET)
        for g in sorted({g["GroupId"] for g in inst.get("SecurityGroups") or []}):
            if g in known:
                edge(iid, g, EdgeKind.PROTECTED_BY)

    for lb in raw.load_balancers:
        arn = lb["LoadBalancerArn"]
        targets = sorted(set(raw.lb_targets.get(arn, [])))
        add(
            ResourceType.LOAD_BALANCER,
            arn,
            lb.get("LoadBalancerName"),
            LoadBalancerSpec(
                kind=lb.get("Type"),
                scheme=lb.get("Scheme"),
                dns_name=lb.get("DNSName"),
                network_native_id=lb.get("VpcId"),
                target_native_ids=targets,
            ),
            lb,
            status=(lb.get("State") or {}).get("Code"),
            created_at_source=_dt(lb.get("CreatedTime")),
        )
        for t in targets:
            if t in known:
                edge(arn, t, EdgeKind.ROUTES_TO)
        for az in lb.get("AvailabilityZones") or []:
            if az.get("SubnetId") in known:
                edge(arn, az["SubnetId"], EdgeKind.IN_SUBNET)
        for g in lb.get("SecurityGroups") or []:
            if g in known:
                edge(arn, g, EdgeKind.PROTECTED_BY)

    return bundle


def _nic(n: Raw) -> NicSpec:
    private = [a["PrivateIpAddress"] for a in n.get("PrivateIpAddresses") or [] if a.get("PrivateIpAddress")]
    public = sorted(
        {
            ip
            for ip in [(n.get("Association") or {}).get("PublicIp")]
            + [(a.get("Association") or {}).get("PublicIp") for a in n.get("PrivateIpAddresses") or []]
            if ip
        }
    )
    return NicSpec(
        native_id=n.get("NetworkInterfaceId"),
        subnet_native_id=n.get("SubnetId"),
        private_ips=private or ([n["PrivateIpAddress"]] if n.get("PrivateIpAddress") else []),
        public_ips=public,
        security_group_native_ids=[g["GroupId"] for g in n.get("Groups") or []],
        source_dest_check=n.get("SourceDestCheck"),
    )
