"""EC2 instance normalizer — maps describe_instances response to VMSpec.

Pure function — no network I/O, no DB calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from core.models import (
    DiskSpec,
    EdgeKind,
    NicSpec,
    ProvenanceKind,
    ProviderName,
    ResourceEdge,
    ResourceKind,
    ResourceStatus,
    VMSpec,
)
from core.provenance import ProvenanceTracker

# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

_ARCH_MAP: dict[str, str] = {
    "x86_64": "x86_64",
    "i386": "x86_64",
    "arm64": "arm64",
    "x86_64_mac": "x86_64",
    "arm64_mac": "arm64",
}

_STATUS_MAP: dict[str, ResourceStatus] = {
    "running": ResourceStatus.running,
    "stopped": ResourceStatus.stopped,
    "stopping": ResourceStatus.stopped,
    "terminated": ResourceStatus.terminated,
    "shutting-down": ResourceStatus.terminated,
    "pending": ResourceStatus.unknown,
    "rebooting": ResourceStatus.running,
}

_OS_PLATFORM_MAP: dict[str, str] = {
    "windows": "windows",
    "Windows": "windows",
    "linux/unix": "linux",
    "SUSE Linux": "linux",
    "Red Hat Enterprise Linux": "linux",
    "Linux/UNIX": "linux",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_tags(raw_tags: list[dict[str, str]]) -> dict[str, str]:
    """Convert EC2 Tag list to dict, stripping aws:-prefixed keys."""
    return {
        t["Key"]: t["Value"]
        for t in raw_tags
        if not t.get("Key", "").startswith("aws:")
    }


def _normalize_disks(
    instance: dict[str, Any],
) -> list[DiskSpec]:
    """Build DiskSpec list from BlockDeviceMappings.

    Instance-store (ephemeral) disks are flagged with ephemeral=True.
    """
    disks: list[DiskSpec] = []

    root_device_type = instance.get("RootDeviceType", "")
    root_device_name = instance.get("RootDeviceName", "")

    for mapping in instance.get("BlockDeviceMappings", []):
        ebs = mapping.get("Ebs", {})
        device_name = mapping.get("DeviceName", "")
        is_root = device_name == root_device_name
        disks.append(
            DiskSpec(
                type_class=ebs.get("VolumeType"),
                iops=ebs.get("Iops"),
                boot=is_root,
                encrypted=ebs.get("Encrypted", False),
                kms_key_ref=ebs.get("KmsKeyId"),
                ephemeral=False,  # EBS-backed disks are persistent
            )
        )

    # Instance-store is not enumerated per block device in describe_instances;
    # flag a synthetic entry when root device type is instance-store.
    if root_device_type == "instance-store":
        disks.append(
            DiskSpec(
                boot=True,
                ephemeral=True,
                type_class="instance-store",
            )
        )

    return disks


def _normalize_nics(
    instance: dict[str, Any],
) -> tuple[list[NicSpec], str | None, str | None]:
    """Build NicSpec list and extract primary IP addresses."""
    nics: list[NicSpec] = []
    primary_private: str | None = None
    primary_public: str | None = None

    for idx, iface in enumerate(instance.get("NetworkInterfaces", [])):
        private_ips = [
            a["PrivateIpAddress"]
            for a in iface.get("PrivateIpAddresses", [])
            if "PrivateIpAddress" in a
        ]
        public_ips = [
            a["Association"]["PublicIp"]
            for a in iface.get("PrivateIpAddresses", [])
            if a.get("Association", {}).get("PublicIp")
        ]
        # Also capture the association on the interface itself
        iface_assoc = iface.get("Association", {})
        if iface_assoc.get("PublicIp") and iface_assoc["PublicIp"] not in public_ips:
            public_ips.append(iface_assoc["PublicIp"])

        sg_ids = [g["GroupId"] for g in iface.get("Groups", [])]
        subnet_id = iface.get("SubnetId")

        nics.append(
            NicSpec(
                subnet_id=subnet_id,
                private_ips=private_ips,
                public_ips=public_ips,
                security_group_ids=sg_ids,
                source_dest_check=iface.get("SourceDestCheck", True),
            )
        )

        if idx == 0:
            primary_private = iface.get("PrivateIpAddress") or (private_ips[0] if private_ips else None)
            primary_public = iface_assoc.get("PublicIp") or (public_ips[0] if public_ips else None)

    return nics, primary_private, primary_public


def _resolve_os(instance: dict[str, Any]) -> str | None:
    """Resolve OS family from Platform / PlatformDetails fields."""
    platform = instance.get("Platform", "")
    if platform and platform.lower() == "windows":
        return "windows"
    platform_details = instance.get("PlatformDetails", "")
    if platform_details:
        lc = platform_details.lower()
        if "windows" in lc:
            return "windows"
        if "linux" in lc or "unix" in lc:
            return "linux"
    return "linux"  # default for EC2


# ---------------------------------------------------------------------------
# Main normalizer
# ---------------------------------------------------------------------------


def normalize_ec2_instance(
    raw: dict[str, Any],
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: str = "",
    region: str = "",
) -> tuple[VMSpec, list[ResourceEdge]]:
    """Normalize a single EC2 instance dict (from describe_instances) to VMSpec.

    Args:
        raw: A single instance dict from ``Reservations[].Instances[]``.
        snapshot_id: The UUID of the active discovery snapshot.
        connection_id: The connection UUID.
        workspace_id: The workspace UUID.
        account: AWS account ID (from STS / reservation OwnerId).
        region: The AWS region string (e.g. ``us-east-1``).

    Returns:
        Tuple of (VMSpec, list[ResourceEdge]). The edges describe NIC→subnet
        (in_subnet) and NIC→security_group (protected_by) relationships.

    Note:
        Tags from customer clouds are marked untrusted=True in provenance.
        The ``name`` field is derived from the ``Name`` tag.
    """
    tracker = ProvenanceTracker()
    source = "ec2:describe_instances"

    instance_id = raw["InstanceId"]
    resource_id = uuid.uuid4()

    # ---- Tags ---------------------------------------------------------------
    raw_tags = raw.get("Tags", [])
    tags = _extract_tags(raw_tags)
    name = tags.get("Name")
    tracker.mark("tags", ProvenanceKind.discovered, source=source)
    tracker.mark("name", ProvenanceKind.discovered, source=source)

    # ---- Status -------------------------------------------------------------
    state_name = raw.get("State", {}).get("Name", "unknown")
    status = _STATUS_MAP.get(state_name, ResourceStatus.unknown)
    tracker.mark("status", ProvenanceKind.discovered, source=source)

    # ---- Placement ----------------------------------------------------------
    placement = raw.get("Placement", {})
    zone = placement.get("AvailabilityZone")
    tenancy = placement.get("Tenancy", "default")
    tracker.mark("zone", ProvenanceKind.discovered, source=source)
    tracker.mark("tenancy", ProvenanceKind.discovered, source=source)

    # ---- Lifecycle ----------------------------------------------------------
    instance_lifecycle = raw.get("InstanceLifecycle", "")
    lifecycle_extra = "on-demand"
    if instance_lifecycle == "spot":
        lifecycle_extra = "spot"
    elif instance_lifecycle == "scheduled":
        lifecycle_extra = "scheduled"
    tracker.mark("lifecycle", ProvenanceKind.discovered, source=source)

    # ---- Architecture -------------------------------------------------------
    raw_arch = raw.get("Architecture", "")
    architecture = _ARCH_MAP.get(raw_arch, raw_arch or None)
    tracker.mark("architecture", ProvenanceKind.discovered, source=source)

    # ---- CPU ----------------------------------------------------------------
    cpu_opts = raw.get("CpuOptions", {})
    core_count = cpu_opts.get("CoreCount")
    threads_per_core = cpu_opts.get("ThreadsPerCore")
    vcpu: int | None = None
    if core_count is not None and threads_per_core is not None:
        vcpu = int(core_count) * int(threads_per_core)
        tracker.mark("vcpu", ProvenanceKind.derived, source=source)
    else:
        tracker.mark("vcpu", ProvenanceKind.inferred, confidence=0.0, source=source)

    # ---- Memory (not in describe_instances, marked inferred) ----------------
    # Memory comes from instance-type catalog (Phase 2b). Mark as inferred now.
    tracker.mark("memory_gib", ProvenanceKind.inferred, confidence=0.0, source="catalog:pending")

    # ---- OS -----------------------------------------------------------------
    os_family = _resolve_os(raw)
    tracker.mark("os_name", ProvenanceKind.discovered, source=source)

    # ---- Instance type (SKU) ------------------------------------------------
    instance_type = raw.get("InstanceType")
    tracker.mark("instance_type", ProvenanceKind.discovered, source=source)

    # ---- Image --------------------------------------------------------------
    image_id = raw.get("ImageId")
    tracker.mark("image_id", ProvenanceKind.discovered, source=source)

    # ---- Boot mode ----------------------------------------------------------
    boot_mode = raw.get("BootMode")

    # ---- Identity / IAM profile ---------------------------------------------
    iam_profile = raw.get("IamInstanceProfile") or {}
    identity_arn = iam_profile.get("Arn")

    # ---- Disks --------------------------------------------------------------
    disks = _normalize_disks(raw)
    tracker.mark("disks", ProvenanceKind.discovered, source=source)

    # ---- NICs ---------------------------------------------------------------
    nics, primary_private_ip, primary_public_ip = _normalize_nics(raw)
    tracker.mark("nics", ProvenanceKind.discovered, source=source)
    tracker.mark("primary_private_ip", ProvenanceKind.discovered, source=source)
    tracker.mark("primary_public_ip", ProvenanceKind.discovered, source=source)

    # ---- Timestamps ---------------------------------------------------------
    launch_time: datetime | None = None
    if raw.get("LaunchTime"):
        lt = raw["LaunchTime"]
        if isinstance(lt, str):
            # boto3 returns datetime objects but fixtures may be strings
            try:
                launch_time = datetime.fromisoformat(lt.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None
        elif isinstance(lt, datetime):
            launch_time = lt.replace(tzinfo=UTC) if lt.tzinfo is None else lt

    # ---- Build VMSpec -------------------------------------------------------
    extra: dict[str, Any] = {
        "lifecycle": lifecycle_extra,
        "boot_mode": boot_mode,
        "identity_arn": identity_arn,
        "hypervisor": raw.get("Hypervisor"),
        "virtualization_type": raw.get("VirtualizationType"),
        "ena_support": raw.get("EnaSupport"),
        "ebs_optimized": raw.get("EbsOptimized"),
        "source_dest_check": raw.get("SourceDestCheck"),
    }

    spec = VMSpec(
        id=resource_id,
        workspace_id=workspace_id,
        connection_id=connection_id,
        provider=ProviderName.aws,
        native_id=instance_id,
        account=account,
        region=region,
        zone=zone,
        type=ResourceKind.vm,
        name=name,
        status=status,
        tags=tags,
        created_at_source=launch_time,
        snapshot_id=snapshot_id,
        vcpu=vcpu,
        memory_gib=None,  # populated from catalog in Phase 2b
        instance_type=instance_type,
        architecture=architecture,
        os_name=os_family,
        image_id=image_id,
        nics=nics,
        primary_private_ip=primary_private_ip,
        primary_public_ip=primary_public_ip,
        disks=disks,
        tenancy=tenancy,
        provenance=tracker.build(),
        extra=extra,
    )

    # ---- Build edges --------------------------------------------------------
    edges: list[ResourceEdge] = []
    for nic in nics:
        if nic.subnet_id:
            # NIC → subnet: in_subnet (use native_id placeholder; resolved after full pass)
            edges.append(
                ResourceEdge(
                    from_id=resource_id,
                    to_id=_subnet_placeholder_uuid(nic.subnet_id),
                    kind=EdgeKind.in_subnet,
                    workspace_id=workspace_id,
                    snapshot_id=snapshot_id,
                )
            )
        for sg_id in nic.security_group_ids:
            edges.append(
                ResourceEdge(
                    from_id=resource_id,
                    to_id=_sg_placeholder_uuid(sg_id),
                    kind=EdgeKind.protected_by,
                    workspace_id=workspace_id,
                    snapshot_id=snapshot_id,
                )
            )

    return spec, edges


# ---------------------------------------------------------------------------
# Stable UUID helpers for placeholder edge targets
# ---------------------------------------------------------------------------
# Edges are created with deterministic UUIDs derived from native IDs so that
# the discovery pipeline can resolve them after the full collection pass.
# UUIDv5 with the DNS namespace is used (no secrets involved).


def _subnet_placeholder_uuid(subnet_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"subnet:{subnet_id}")


def _sg_placeholder_uuid(sg_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"sg:{sg_id}")
