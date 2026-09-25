"""Security group normalizer — maps describe_security_groups response.

Pure function — no network I/O, no DB calls.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.models import (
    ProvenanceKind,
    ProviderName,
    Resource,
    ResourceEdge,
    ResourceKind,
    ResourceStatus,
    SecurityRule,
)
from core.provenance import ProvenanceTracker


def _normalize_ip_permissions(
    permissions: list[dict[str, Any]],
    direction: str,
) -> list[SecurityRule]:
    """Convert IpPermissions / IpPermissionsEgress to SecurityRule list."""
    rules: list[SecurityRule] = []

    for perm in permissions:
        protocol = perm.get("IpProtocol", "-1")
        from_port = perm.get("FromPort")
        to_port = perm.get("ToPort")

        # CIDR-based rules
        for ip_range in perm.get("IpRanges", []):
            rules.append(
                SecurityRule(
                    direction=direction,
                    protocol=protocol,
                    port_from=from_port,
                    port_to=to_port,
                    peer_cidr=ip_range.get("CidrIp"),
                    action="allow",
                )
            )
        for ipv6_range in perm.get("Ipv6Ranges", []):
            rules.append(
                SecurityRule(
                    direction=direction,
                    protocol=protocol,
                    port_from=from_port,
                    port_to=to_port,
                    peer_cidr=ipv6_range.get("CidrIpv6"),
                    action="allow",
                )
            )

        # Security-group references
        for sg_pair in perm.get("UserIdGroupPairs", []):
            rules.append(
                SecurityRule(
                    direction=direction,
                    protocol=protocol,
                    port_from=from_port,
                    port_to=to_port,
                    peer_group=sg_pair.get("GroupId"),
                    action="allow",
                )
            )

    return rules


def normalize_security_group(
    raw: dict[str, Any],
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: str = "",
    region: str = "",
) -> tuple[Resource, list[SecurityRule], list[ResourceEdge]]:
    """Normalize a single security group dict from describe_security_groups.

    Args:
        raw: A single SG dict from ``SecurityGroups[]``.
        snapshot_id: The UUID of the active discovery snapshot.
        connection_id: The connection UUID.
        workspace_id: The workspace UUID.
        account: AWS account ID.
        region: The AWS region string.

    Returns:
        Tuple of (Resource, list[SecurityRule], list[ResourceEdge]).
        Rules are stored in resource.extra["rules"]. Edges include SG → VPC.
    """
    tracker = ProvenanceTracker()
    source = "ec2:describe_security_groups"

    sg_id = raw["GroupId"]
    resource_id = uuid.uuid4()

    raw_tags = raw.get("Tags", [])
    tags = {
        t["Key"]: t["Value"]
        for t in raw_tags
        if not t.get("Key", "").startswith("aws:")
    }
    tracker.mark_all(["tags", "name", "status"], ProvenanceKind.discovered, source=source)

    # SGs are always "active" once they exist
    name = raw.get("GroupName") or tags.get("Name")

    ingress_rules = _normalize_ip_permissions(
        raw.get("IpPermissions", []), "ingress"
    )
    egress_rules = _normalize_ip_permissions(
        raw.get("IpPermissionsEgress", []), "egress"
    )
    all_rules = ingress_rules + egress_rules

    resource = Resource(
        id=resource_id,
        workspace_id=workspace_id,
        connection_id=connection_id,
        provider=ProviderName.aws,
        native_id=sg_id,
        account=account,
        region=region,
        type=ResourceKind.security_group,
        name=name,
        status=ResourceStatus.running,
        tags=tags,
        snapshot_id=snapshot_id,
    )

    edges: list[ResourceEdge] = []
    vpc_id = raw.get("VpcId")
    if vpc_id:
        from core.models import EdgeKind

        edges.append(
            ResourceEdge(
                from_id=resource_id,
                to_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"vpc:{vpc_id}"),
                kind=EdgeKind.in_subnet,
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
            )
        )

    return resource, all_rules, edges
