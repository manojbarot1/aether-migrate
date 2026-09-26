"""Provider-neutral inventory model (PROJECT_PLAN §10).

Everything here is pure data. Provider adapters produce :class:`NormalizedBundle`s;
the persistence layer stores them; engines (sizing, cost, assessment) consume them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

# Namespace for deterministic resource ids: uuid5(namespace, f"{snapshot_id}:{native_id}").
_RESOURCE_NS = uuid.UUID("6f1c7d8e-3b0a-4c55-9a2e-0c1f5e7a9b31")


def resource_id(snapshot_id: uuid.UUID, native_id: str) -> uuid.UUID:
    """Stable id for a resource within a snapshot, so edges can be built without a DB round-trip."""
    return uuid.uuid5(_RESOURCE_NS, f"{snapshot_id}:{native_id}")


class ResourceType(StrEnum):
    VM = "vm"
    DISK = "disk"
    NIC = "nic"
    NETWORK = "network"  # VPC / VNet
    SUBNET = "subnet"
    SECURITY_GROUP = "security_group"
    LOAD_BALANCER = "load_balancer"


class EdgeKind(StrEnum):
    ATTACHED_TO = "attached_to"  # disk/nic -> vm
    IN_SUBNET = "in_subnet"  # vm/nic/lb -> subnet
    IN_NETWORK = "in_network"  # subnet/sg -> network
    PROTECTED_BY = "protected_by"  # vm/nic/lb -> security group
    ROUTES_TO = "routes_to"  # load balancer -> vm
    REFERENCES = "references"  # security group -> security group (rule peer)


class Provenance(StrEnum):
    DISCOVERED = "discovered"
    DERIVED = "derived"
    INFERRED = "inferred"
    USER_PROVIDED = "user_provided"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiskSpec(_Spec):
    native_id: str | None = None
    device: str | None = None
    size_gib: int | None = None
    type_class: str | None = None
    iops: int | None = None
    throughput_mbps: int | None = None
    boot: bool = False
    encrypted: bool = False
    kms_key_ref: str | None = None
    ephemeral: bool = False  # instance store / temp disk: data does not survive migration
    delete_on_termination: bool | None = None


class NicSpec(_Spec):
    native_id: str | None = None
    subnet_native_id: str | None = None
    private_ips: list[str] = Field(default_factory=list)
    public_ips: list[str] = Field(default_factory=list)
    security_group_native_ids: list[str] = Field(default_factory=list)
    source_dest_check: bool | None = None
    accelerated_networking: bool | None = None


class SecurityRule(_Spec):
    direction: Literal["ingress", "egress"]
    protocol: str  # "tcp" | "udp" | "icmp" | "all" | number as string
    port_from: int | None = None
    port_to: int | None = None
    peer_cidr: str | None = None
    peer_group_native_id: str | None = None
    peer_prefix_list: str | None = None
    action: Literal["allow", "deny"] = "allow"
    description: str | None = None


class VmSpec(_Spec):
    source_sku: str | None = None
    vcpu: int | None = None
    memory_mib: int | None = None
    cpu_arch: Literal["x86_64", "arm64"] | None = None
    cpu_vendor: str | None = None
    gpu_model: str | None = None
    gpu_count: int = 0
    hypervisor: str | None = None
    tenancy: str | None = None
    lifecycle: Literal["on_demand", "spot", "scheduled", "reserved_unknown"] = "on_demand"
    network_performance: str | None = None
    local_storage_gib: int | None = None

    os_family: Literal["linux", "windows", "unknown"] = "unknown"
    os_distribution: str | None = None
    os_version: str | None = None
    license_model: Literal["included", "byol", "none", "unknown"] = "unknown"
    platform_details: str | None = None
    boot_mode: Literal["bios", "uefi", "uefi_preferred", "unknown"] = "unknown"
    image_ref: str | None = None
    image_name: str | None = None

    disks: list[DiskSpec] = Field(default_factory=list)
    nics: list[NicSpec] = Field(default_factory=list)
    instance_identity: str | None = None  # instance profile / managed identity / service account
    launched_at: datetime | None = None

    provenance: dict[str, Provenance] = Field(default_factory=dict)


class NetworkSpec(_Spec):
    cidrs: list[str] = Field(default_factory=list)
    is_default: bool = False


class SubnetSpec(_Spec):
    cidr: str | None = None
    network_native_id: str | None = None
    public_ip_on_launch: bool | None = None


class SecurityGroupSpec(_Spec):
    network_native_id: str | None = None
    description: str | None = None
    rules: list[SecurityRule] = Field(default_factory=list)


class LoadBalancerSpec(_Spec):
    kind: str | None = None  # application / network / gateway / classic
    scheme: str | None = None  # internet-facing / internal
    dns_name: str | None = None
    network_native_id: str | None = None
    listeners: list[dict[str, Any]] = Field(default_factory=list)
    target_native_ids: list[str] = Field(default_factory=list)


SPEC_MODELS: dict[ResourceType, type[_Spec]] = {
    ResourceType.VM: VmSpec,
    ResourceType.DISK: DiskSpec,
    ResourceType.NIC: NicSpec,
    ResourceType.NETWORK: NetworkSpec,
    ResourceType.SUBNET: SubnetSpec,
    ResourceType.SECURITY_GROUP: SecurityGroupSpec,
    ResourceType.LOAD_BALANCER: LoadBalancerSpec,
}


class NormalizedResource(BaseModel):
    id: uuid.UUID
    type: ResourceType
    native_id: str
    name: str | None = None
    account: str
    region: str
    zone: str | None = None
    status: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    spec: dict[str, Any]
    raw: dict[str, Any]
    created_at_source: datetime | None = None


class NormalizedEdge(BaseModel):
    from_id: uuid.UUID
    to_id: uuid.UUID
    kind: EdgeKind


class CoverageStatus(StrEnum):
    OK = "ok"
    DENIED = "denied"
    DISABLED = "disabled"
    THROTTLED_PARTIAL = "throttled_partial"
    ERROR = "error"


class CoverageEntry(BaseModel):
    region: str
    kind: str  # resource family, e.g. "ec2:instances"
    status: CoverageStatus
    detail: str | None = None


class NormalizedBundle(BaseModel):
    resources: list[NormalizedResource] = Field(default_factory=list)
    edges: list[NormalizedEdge] = Field(default_factory=list)
    coverage: list[CoverageEntry] = Field(default_factory=list)
