"""EBS volume normalizer — maps describe_volumes response to DiskSpec.

Pure function — no network I/O, no DB calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from core.models import (
    DiskSpec,
    EdgeKind,
    ProvenanceKind,
    ProviderName,
    Resource,
    ResourceEdge,
    ResourceKind,
    ResourceStatus,
)
from core.provenance import ProvenanceTracker


def normalize_ebs_volume(
    raw: dict[str, Any],
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: str = "",
    region: str = "",
) -> tuple[Resource, list[ResourceEdge]]:
    """Normalize a single EBS volume dict from describe_volumes.

    Args:
        raw: A single volume dict from ``Volumes[]``.
        snapshot_id: The UUID of the active discovery snapshot.
        connection_id: The connection UUID.
        workspace_id: The workspace UUID.
        account: AWS account ID.
        region: The AWS region string.

    Returns:
        Tuple of (Resource with DiskSpec in extra, list[ResourceEdge]).
        Edges describe attachment to EC2 instances (attached_to).
    """
    tracker = ProvenanceTracker()
    source = "ec2:describe_volumes"

    volume_id = raw["VolumeId"]
    resource_id = uuid.uuid4()

    # ---- Tags ---------------------------------------------------------------
    raw_tags = raw.get("Tags", [])
    tags = {
        t["Key"]: t["Value"]
        for t in raw_tags
        if not t.get("Key", "").startswith("aws:")
    }
    name = tags.get("Name")
    tracker.mark("tags", ProvenanceKind.discovered, source=source)

    # ---- State --------------------------------------------------------------
    state = raw.get("State", "unknown")
    status_map = {
        "available": ResourceStatus.stopped,
        "in-use": ResourceStatus.running,
        "creating": ResourceStatus.unknown,
        "deleting": ResourceStatus.terminated,
        "deleted": ResourceStatus.terminated,
        "error": ResourceStatus.unknown,
    }
    status = status_map.get(state, ResourceStatus.unknown)
    tracker.mark_all(
        ["status", "size_gib", "type_class", "iops", "encrypted", "zone"],
        ProvenanceKind.discovered,
        source=source,
    )

    # ---- Timestamps ---------------------------------------------------------
    create_time: datetime | None = None
    if raw.get("CreateTime"):
        ct = raw["CreateTime"]
        if isinstance(ct, str):
            try:
                create_time = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            except ValueError:
                create_time = None
        elif isinstance(ct, datetime):
            create_time = ct.replace(tzinfo=UTC) if ct.tzinfo is None else ct

    # ---- DiskSpec embedded in extra -----------------------------------------
    disk_spec = DiskSpec(
        size_gib=raw.get("Size"),
        type_class=raw.get("VolumeType"),
        iops=raw.get("Iops"),
        throughput_mbps=raw.get("Throughput"),
        encrypted=raw.get("Encrypted", False),
        kms_key_ref=raw.get("KmsKeyId") or None,
        ephemeral=False,
    )

    resource = Resource(
        id=resource_id,
        workspace_id=workspace_id,
        connection_id=connection_id,
        provider=ProviderName.aws,
        native_id=volume_id,
        account=account,
        region=region,
        zone=raw.get("AvailabilityZone"),
        type=ResourceKind.disk,
        name=name,
        status=status,
        tags=tags,
        created_at_source=create_time,
        snapshot_id=snapshot_id,
        raw_ref=None,
    )

    # ---- Edges: attachment to EC2 instances ---------------------------------
    edges: list[ResourceEdge] = []
    for attachment in raw.get("Attachments", []):
        instance_id = attachment.get("InstanceId")
        if instance_id:
            edges.append(
                ResourceEdge(
                    from_id=resource_id,
                    to_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"ec2:{instance_id}"),
                    kind=EdgeKind.attached_to,
                    workspace_id=workspace_id,
                    snapshot_id=snapshot_id,
                )
            )

    return resource, edges
