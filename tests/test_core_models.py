"""Tests for core Pydantic domain models.

Validates that:
- VMSpec round-trips through JSON without data loss
- NormalizedBundle carries resources and edges correctly
- ProvenanceKind and ResourceKind enum values are stable
- ProvenanceTracker accumulates fields correctly
"""

from __future__ import annotations

import uuid

from core.models import (
    DiskSpec,
    EdgeKind,
    MetricsSpec,
    NicSpec,
    NormalizedBundle,
    ProvenanceField,
    ProvenanceKind,
    ProviderName,
    ResourceEdge,
    ResourceKind,
    ResourceStatus,
    VMSpec,
)
from core.provenance import ProvenanceTracker

# ---------------------------------------------------------------------------
# Enum stability
# ---------------------------------------------------------------------------


def test_provider_name_values() -> None:
    assert ProviderName.aws.value == "aws"
    assert ProviderName.azure.value == "azure"
    assert ProviderName.gcp.value == "gcp"
    assert ProviderName.ibm.value == "ibm"


def test_resource_kind_values() -> None:
    assert ResourceKind.vm.value == "vm"
    assert ResourceKind.disk.value == "disk"
    assert ResourceKind.nic.value == "nic"


def test_provenance_kind_values() -> None:
    assert ProvenanceKind.discovered.value == "discovered"
    assert ProvenanceKind.derived.value == "derived"
    assert ProvenanceKind.inferred.value == "inferred"
    assert ProvenanceKind.user_provided.value == "user_provided"


def test_edge_kind_values() -> None:
    assert EdgeKind.attached_to.value == "attached_to"
    assert EdgeKind.in_subnet.value == "in_subnet"


# ---------------------------------------------------------------------------
# VMSpec
# ---------------------------------------------------------------------------


def _make_vm_spec() -> VMSpec:
    ws = uuid.uuid4()
    conn = uuid.uuid4()
    return VMSpec(
        workspace_id=ws,
        connection_id=conn,
        provider=ProviderName.aws,
        native_id="i-0123456789abcdef0",
        account="123456789012",
        region="us-east-1",
        zone="us-east-1a",
        name="my-vm",
        status=ResourceStatus.running,
        tags={"Env": "prod", "Owner": "alice"},
        instance_type="m5.xlarge",
        vcpu=4,
        memory_gib=16.0,
        architecture="x86_64",
        disks=[
            DiskSpec(size_gib=50.0, type_class="gp3", boot=True, encrypted=True)
        ],
        nics=[
            NicSpec(
                private_ips=["10.0.1.42"],
                public_ips=["1.2.3.4"],
                security_group_ids=["sg-abc123"],
            )
        ],
        metrics=MetricsSpec(cpu_p95=12.5, mem_p95=45.0, window_days=14),
        provenance={
            "vcpu": ProvenanceField(kind=ProvenanceKind.discovered, source="ec2:describe_instances"),
        },
    )


def test_vmspec_round_trip_json() -> None:
    """VMSpec must serialize to JSON and deserialize back without data loss."""
    original = _make_vm_spec()
    json_str = original.model_dump_json()
    restored = VMSpec.model_validate_json(json_str)

    assert restored.native_id == original.native_id
    assert restored.vcpu == original.vcpu
    assert restored.memory_gib == original.memory_gib
    assert len(restored.disks) == 1
    assert restored.disks[0].size_gib == 50.0
    assert restored.disks[0].boot is True
    assert len(restored.nics) == 1
    assert restored.nics[0].private_ips == ["10.0.1.42"]
    assert restored.metrics is not None
    assert restored.metrics.cpu_p95 == 12.5
    assert restored.provenance["vcpu"].kind == ProvenanceKind.discovered


def test_vmspec_tags_preserved() -> None:
    vm = _make_vm_spec()
    assert vm.tags["Env"] == "prod"
    assert vm.tags["Owner"] == "alice"


def test_vmspec_defaults() -> None:
    """VMSpec with only required fields must not raise."""
    vm = VMSpec(
        workspace_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        provider=ProviderName.gcp,
        native_id="projects/my-project/zones/us-central1-a/instances/my-instance",
        account="my-project",
        region="us-central1",
        type=ResourceKind.vm,
    )
    assert vm.status == ResourceStatus.unknown
    assert vm.tags == {}
    assert vm.schema_version == "1.0"


# ---------------------------------------------------------------------------
# NormalizedBundle
# ---------------------------------------------------------------------------


def test_normalized_bundle_contains_resources_and_edges() -> None:
    """NormalizedBundle must correctly hold resources and edges."""
    ws = uuid.uuid4()
    conn = uuid.uuid4()

    vm = VMSpec(
        workspace_id=ws,
        connection_id=conn,
        provider=ProviderName.aws,
        native_id="i-abc",
        account="123",
        region="eu-west-1",
        type=ResourceKind.vm,
    )
    disk = VMSpec(
        workspace_id=ws,
        connection_id=conn,
        provider=ProviderName.aws,
        native_id="vol-xyz",
        account="123",
        region="eu-west-1",
        type=ResourceKind.disk,
    )
    edge = ResourceEdge(
        from_id=disk.id,
        to_id=vm.id,
        kind=EdgeKind.attached_to,
        workspace_id=ws,
    )

    bundle = NormalizedBundle(resources=[vm, disk], edges=[edge])

    assert len(bundle.resources) == 2
    assert len(bundle.edges) == 1
    assert bundle.edges[0].kind == EdgeKind.attached_to
    assert bundle.edges[0].from_id == disk.id
    assert bundle.edges[0].to_id == vm.id


def test_normalized_bundle_empty_is_valid() -> None:
    bundle = NormalizedBundle()
    assert bundle.resources == []
    assert bundle.edges == []


# ---------------------------------------------------------------------------
# ProvenanceTracker
# ---------------------------------------------------------------------------


def test_provenance_tracker_marks_fields() -> None:
    tracker = ProvenanceTracker()
    tracker.mark("vcpu", ProvenanceKind.discovered, source="ec2:describe_instances")
    tracker.mark("cpu_p95", ProvenanceKind.inferred, confidence=0.75, source="cloudwatch")

    result = tracker.build()

    assert "vcpu" in result
    assert result["vcpu"].kind == ProvenanceKind.discovered
    assert result["vcpu"].confidence == 1.0
    assert result["cpu_p95"].confidence == 0.75


def test_provenance_tracker_mark_all() -> None:
    tracker = ProvenanceTracker()
    tracker.mark_all(["vcpu", "memory_gib", "instance_type"], ProvenanceKind.discovered)

    result = tracker.build()
    assert len(result) == 3
    for field in ["vcpu", "memory_gib", "instance_type"]:
        assert result[field].kind == ProvenanceKind.discovered


def test_provenance_tracker_len() -> None:
    tracker = ProvenanceTracker()
    assert len(tracker) == 0
    tracker.mark("vcpu", ProvenanceKind.discovered)
    assert len(tracker) == 1
