"""VPC and subnet normalizers — maps describe_vpcs / describe_subnets responses.

Pure functions — no network I/O, no DB calls.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.models import (
    EdgeKind,
    ProvenanceKind,
    ProviderName,
    Resource,
    ResourceEdge,
    ResourceKind,
    ResourceStatus,
)
from core.provenance import ProvenanceTracker


def normalize_vpc(
    raw: dict[str, Any],
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: str = "",
    region: str = "",
) -> tuple[Resource, list[ResourceEdge]]:
    """Normalize a single VPC dict from describe_vpcs.

    Args:
        raw: A single VPC dict from ``Vpcs[]``.
        snapshot_id: The UUID of the active discovery snapshot.
        connection_id: The connection UUID.
        workspace_id: The workspace UUID.
        account: AWS account ID.
        region: The AWS region string.

    Returns:
        Tuple of (Resource, list[ResourceEdge]).
    """
    tracker = ProvenanceTracker()
    source = "ec2:describe_vpcs"

    vpc_id = raw["VpcId"]
    resource_id = uuid.uuid4()

    raw_tags = raw.get("Tags", [])
    tags = {
        t["Key"]: t["Value"]
        for t in raw_tags
        if not t.get("Key", "").startswith("aws:")
    }
    name = tags.get("Name")
    tracker.mark_all(["tags", "name", "status"], ProvenanceKind.discovered, source=source)

    state = raw.get("State", "available")
    status = ResourceStatus.running if state == "available" else ResourceStatus.unknown

    resource = Resource(
        id=resource_id,
        workspace_id=workspace_id,
        connection_id=connection_id,
        provider=ProviderName.aws,
        native_id=vpc_id,
        account=account,
        region=region,
        type=ResourceKind.network,
        name=name,
        status=status,
        tags=tags,
        snapshot_id=snapshot_id,
    )

    return resource, []


def normalize_subnet(
    raw: dict[str, Any],
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: str = "",
    region: str = "",
) -> tuple[Resource, list[ResourceEdge]]:
    """Normalize a single subnet dict from describe_subnets.

    Args:
        raw: A single subnet dict from ``Subnets[]``.
        snapshot_id: The UUID of the active discovery snapshot.
        connection_id: The connection UUID.
        workspace_id: The workspace UUID.
        account: AWS account ID.
        region: The AWS region string.

    Returns:
        Tuple of (Resource, list[ResourceEdge]). Edges include subnet → VPC
        (routes_to relationship).
    """
    tracker = ProvenanceTracker()
    source = "ec2:describe_subnets"

    subnet_id = raw["SubnetId"]
    resource_id = uuid.uuid4()

    raw_tags = raw.get("Tags", [])
    tags = {
        t["Key"]: t["Value"]
        for t in raw_tags
        if not t.get("Key", "").startswith("aws:")
    }
    name = tags.get("Name")
    tracker.mark_all(["tags", "name", "status", "zone"], ProvenanceKind.discovered, source=source)

    state = raw.get("State", "available")
    status = ResourceStatus.running if state == "available" else ResourceStatus.unknown

    resource = Resource(
        id=resource_id,
        workspace_id=workspace_id,
        connection_id=connection_id,
        provider=ProviderName.aws,
        native_id=subnet_id,
        account=account,
        region=region,
        zone=raw.get("AvailabilityZone"),
        type=ResourceKind.subnet,
        name=name,
        status=status,
        tags=tags,
        snapshot_id=snapshot_id,
    )

    edges: list[ResourceEdge] = []
    vpc_id = raw.get("VpcId")
    if vpc_id:
        edges.append(
            ResourceEdge(
                from_id=resource_id,
                to_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"vpc:{vpc_id}"),
                kind=EdgeKind.routes_to,
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
            )
        )

    return resource, edges
