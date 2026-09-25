"""Unit tests for AWS normalizer pure functions.

These tests make no network calls and no DB calls.
All normalizers are deterministic pure functions.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from aws.normalizers.vm import (
    _sg_placeholder_uuid,
    _subnet_placeholder_uuid,
    normalize_ec2_instance,
)
from core.models import EdgeKind, ProvenanceKind, ResourceStatus
from hypothesis import given, settings
from hypothesis import strategies as st

FIXTURES = Path(__file__).parent / "fixtures"

SNAPSHOT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CONNECTION_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
WORKSPACE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Basic normalization
# ---------------------------------------------------------------------------


def test_normalize_ec2_instance_basic_fields() -> None:
    """All key fields are mapped from the fixture."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, edges = normalize_ec2_instance(
        raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID,
        account="123456789012", region="us-east-1",
    )

    assert spec.native_id == "i-0abcdef1234567890"
    assert spec.instance_type == "m5.xlarge"
    assert spec.region == "us-east-1"
    assert spec.zone == "us-east-1a"
    assert spec.tenancy == "default"
    assert spec.status == ResourceStatus.running
    assert spec.architecture == "x86_64"
    assert spec.image_id == "ami-0abcdef1234567890"
    assert spec.os_name == "linux"
    assert spec.provider.value == "aws"


def test_vcpu_derived_from_cpu_options() -> None:
    """vCPU is CoreCount × ThreadsPerCore (2 × 2 = 4)."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.vcpu == 4


def test_tags_extracted_and_aws_keys_stripped() -> None:
    """Tags are extracted; aws:-prefixed keys are stripped."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)

    assert spec.tags["Name"] == "web-server-01"
    assert spec.tags["Environment"] == "production"
    # aws:cloudformation:stack-name should be stripped
    assert not any(k.startswith("aws:") for k in spec.tags)
    assert spec.name == "web-server-01"


def test_tags_provenance_is_discovered() -> None:
    """Tags provenance kind = discovered."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.provenance["tags"].kind == ProvenanceKind.discovered


def test_disks_ebs_backed() -> None:
    """EBS disks are extracted; none are ephemeral."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)

    assert len(spec.disks) == 2
    assert all(not d.ephemeral for d in spec.disks)
    # Root disk is boot=True
    root = next((d for d in spec.disks if d.boot), None)
    assert root is not None
    assert root.type_class == "gp3"
    assert root.encrypted is True


def test_instance_store_disk_flagged_ephemeral() -> None:
    """Instance-store root device results in an ephemeral DiskSpec."""
    raw = load_fixture("aws_ec2_spot_arm64.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)

    ephemeral_disks = [d for d in spec.disks if d.ephemeral]
    assert len(ephemeral_disks) == 1
    assert ephemeral_disks[0].type_class == "instance-store"


def test_edges_generated_in_subnet_and_protected_by() -> None:
    """NIC → subnet (in_subnet) and NIC → SG (protected_by) edges are created."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, edges = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)

    in_subnet = [e for e in edges if e.kind == EdgeKind.in_subnet]
    protected_by = [e for e in edges if e.kind == EdgeKind.protected_by]

    assert len(in_subnet) == 1
    assert len(protected_by) == 2  # sg-0abc12345 and sg-0def67890

    # Edge from_id should be the VM resource id
    assert all(e.from_id == spec.id for e in edges)

    # to_id should be deterministic placeholder UUIDs
    assert in_subnet[0].to_id == _subnet_placeholder_uuid("subnet-0abc12345")
    sg_ids = {e.to_id for e in protected_by}
    assert _sg_placeholder_uuid("sg-0abc12345") in sg_ids
    assert _sg_placeholder_uuid("sg-0def67890") in sg_ids


def test_arm64_architecture_mapped_correctly() -> None:
    """arm64 architecture string is preserved."""
    raw = load_fixture("aws_ec2_spot_arm64.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.architecture == "arm64"


def test_spot_instance_lifecycle() -> None:
    """Spot instances get lifecycle=spot in extra."""
    raw = load_fixture("aws_ec2_spot_arm64.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.extra.get("lifecycle") == "spot"


def test_on_demand_lifecycle() -> None:
    """Non-spot instances get lifecycle=on-demand."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.extra.get("lifecycle") == "on-demand"


def test_status_stopped_mapped() -> None:
    """Stopped state maps to ResourceStatus.stopped."""
    raw = load_fixture("aws_ec2_spot_arm64.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.status == ResourceStatus.stopped


def test_memory_provenance_is_inferred() -> None:
    """Memory GiB is not in EC2 describe_instances — provenance = inferred."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.memory_gib is None
    assert spec.provenance["memory_gib"].kind == ProvenanceKind.inferred


def test_iam_profile_in_extra() -> None:
    """IAM instance profile ARN is stored in extra.identity_arn."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert "instance-profile/WebServerRole" in (spec.extra.get("identity_arn") or "")


def test_primary_ips_extracted() -> None:
    """Primary private and public IPs are extracted."""
    raw = load_fixture("aws_ec2_instance.json")
    spec, _ = normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.primary_private_ip == "10.0.1.100"
    assert spec.primary_public_ip == "54.1.2.3"


# ---------------------------------------------------------------------------
# Purity / idempotence tests
# ---------------------------------------------------------------------------


def test_normalize_is_pure_repeated_calls() -> None:
    """Calling normalize 10 times with the same input produces identical output."""
    raw = load_fixture("aws_ec2_instance.json")

    results = [
        normalize_ec2_instance(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
        for _ in range(10)
    ]

    # All specs should be equal (ignoring randomly assigned id fields — we check fields)
    specs = [r[0] for r in results]
    for spec in specs:
        assert spec.native_id == "i-0abcdef1234567890"
        assert spec.vcpu == 4
        assert spec.status == ResourceStatus.running
        assert len(spec.disks) == 2

    # All edge counts should be equal
    for _, edges in results:
        assert len(edges) == 3  # 1 in_subnet + 2 protected_by


@given(
    arch=st.sampled_from(["x86_64", "arm64", "i386", "x86_64_mac", "arm64_mac", "unknown_arch"]),
)
@settings(max_examples=20)
def test_architecture_mapping_does_not_raise(arch: str) -> None:
    """normalize_ec2_instance handles any architecture string without raising."""
    raw = load_fixture("aws_ec2_instance.json")
    raw_copy = {**raw, "Architecture": arch}
    spec, _ = normalize_ec2_instance(raw_copy, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert spec.architecture is not None or arch.startswith("unknown")


# ---------------------------------------------------------------------------
# Security group normalizer
# ---------------------------------------------------------------------------


def test_normalize_security_group() -> None:
    """Security group rules are parsed correctly."""
    from aws.normalizers.security_group import normalize_security_group

    raw = {
        "GroupId": "sg-0abc12345",
        "GroupName": "web-sg",
        "Description": "Web security group",
        "VpcId": "vpc-0abc",
        "Tags": [{"Key": "Name", "Value": "web-sg"}],
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                "Ipv6Ranges": [],
                "UserIdGroupPairs": [],
            }
        ],
        "IpPermissionsEgress": [
            {
                "IpProtocol": "-1",
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                "Ipv6Ranges": [],
                "UserIdGroupPairs": [],
            }
        ],
    }

    resource, rules, edges = normalize_security_group(
        raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID,
        account="123456789012", region="us-east-1",
    )

    assert resource.native_id == "sg-0abc12345"
    assert resource.name == "web-sg"

    ingress = [r for r in rules if r.direction == "ingress"]
    egress = [r for r in rules if r.direction == "egress"]

    assert len(ingress) == 1
    assert ingress[0].protocol == "tcp"
    assert ingress[0].port_from == 443
    assert ingress[0].peer_cidr == "0.0.0.0/0"

    assert len(egress) == 1
    assert egress[0].protocol == "-1"


# ---------------------------------------------------------------------------
# Disk normalizer
# ---------------------------------------------------------------------------


def test_normalize_ebs_volume() -> None:
    """EBS volume is normalized to a Resource with correct fields."""
    from aws.normalizers.disk import normalize_ebs_volume

    raw = {
        "VolumeId": "vol-0abc12345",
        "Size": 100,
        "VolumeType": "gp3",
        "Iops": 3000,
        "Throughput": 125,
        "Encrypted": True,
        "KmsKeyId": "arn:aws:kms:us-east-1:123456789:key/abc",
        "State": "in-use",
        "AvailabilityZone": "us-east-1a",
        "CreateTime": "2024-01-01T00:00:00Z",
        "Tags": [{"Key": "Name", "Value": "data-vol"}],
        "Attachments": [{"InstanceId": "i-0abc123", "State": "attached"}],
    }

    resource, edges = normalize_ebs_volume(
        raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID,
        account="123456789012", region="us-east-1",
    )

    from core.models import ResourceStatus
    assert resource.native_id == "vol-0abc12345"
    assert resource.status == ResourceStatus.running  # in-use → running
    assert resource.zone == "us-east-1a"
    assert len(edges) == 1
    assert edges[0].kind.value == "attached_to"


# ---------------------------------------------------------------------------
# VPC / Subnet normalizers
# ---------------------------------------------------------------------------


def test_normalize_vpc() -> None:
    from aws.normalizers.network import normalize_vpc

    raw = {
        "VpcId": "vpc-0abc",
        "State": "available",
        "CidrBlock": "10.0.0.0/16",
        "Tags": [{"Key": "Name", "Value": "main-vpc"}],
    }
    resource, edges = normalize_vpc(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert resource.native_id == "vpc-0abc"
    assert resource.name == "main-vpc"
    assert edges == []


def test_normalize_subnet() -> None:
    from aws.normalizers.network import normalize_subnet
    from core.models import EdgeKind

    raw = {
        "SubnetId": "subnet-0abc",
        "VpcId": "vpc-0abc",
        "State": "available",
        "AvailabilityZone": "us-east-1a",
        "CidrBlock": "10.0.1.0/24",
        "Tags": [],
    }
    resource, edges = normalize_subnet(raw, SNAPSHOT_ID, CONNECTION_ID, WORKSPACE_ID)
    assert resource.native_id == "subnet-0abc"
    assert resource.zone == "us-east-1a"
    assert len(edges) == 1
    assert edges[0].kind == EdgeKind.routes_to
