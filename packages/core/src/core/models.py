"""Normalized domain models for AETHER MIGRATE.

All cloud provider resources are normalized into these Pydantic v2 models
before being stored or processed. Each model carries provenance metadata
describing how each field was obtained.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return timezone-naive UTC datetime (matches legacy DB column type)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProviderName(str, Enum):
    """Supported cloud providers."""

    aws = "aws"
    azure = "azure"
    gcp = "gcp"
    ibm = "ibm"


class ResourceKind(str, Enum):
    """Normalized resource types."""

    vm = "vm"
    disk = "disk"
    nic = "nic"
    network = "network"
    subnet = "subnet"
    security_group = "security_group"
    load_balancer = "load_balancer"


class ProvenanceKind(str, Enum):
    """How a field value was obtained."""

    discovered = "discovered"      # Read directly from provider API
    derived = "derived"            # Computed from other discovered fields
    inferred = "inferred"          # Estimated / heuristic
    user_provided = "user_provided"  # Supplied by a human via the UI


class ResourceStatus(str, Enum):
    """Lifecycle state of a resource."""

    running = "running"
    stopped = "stopped"
    terminated = "terminated"
    unknown = "unknown"


class EdgeKind(str, Enum):
    """Types of directed relationships between resources."""

    attached_to = "attached_to"
    in_subnet = "in_subnet"
    protected_by = "protected_by"
    behind_lb = "behind_lb"
    routes_to = "routes_to"
    depends_on = "depends_on"


# ---------------------------------------------------------------------------
# Supporting value objects
# ---------------------------------------------------------------------------


class Region(BaseModel):
    """A cloud provider region."""

    model_config = {"frozen": True}

    provider: ProviderName
    name: str
    display_name: str


class ProvenanceField(BaseModel):
    """Metadata describing how a field value was obtained."""

    model_config = {"frozen": True}

    kind: ProvenanceKind
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source: str = ""


class Tag(BaseModel):
    """A key-value tag attached to a cloud resource."""

    model_config = {"frozen": True}

    key: str
    value: str


# ---------------------------------------------------------------------------
# Sub-specifications
# ---------------------------------------------------------------------------


class DiskSpec(BaseModel):
    """Specification for a disk (block storage) resource."""

    size_gib: float | None = None
    type_class: str | None = None          # e.g. "gp3", "Premium_LRS", "pd-ssd"
    iops: int | None = None
    throughput_mbps: int | None = None
    boot: bool = False
    encrypted: bool = False
    kms_key_ref: str | None = None         # ARN / resource-id; not the key material
    ephemeral: bool = False


class NicSpec(BaseModel):
    """Specification for a network interface."""

    subnet_id: str | None = None
    private_ips: list[str] = Field(default_factory=list)
    public_ips: list[str] = Field(default_factory=list)
    security_group_ids: list[str] = Field(default_factory=list)
    accelerated_networking: bool = False
    source_dest_check: bool = True


class SecurityRule(BaseModel):
    """A single ingress or egress rule in a security group / firewall."""

    direction: str                         # "ingress" | "egress"
    protocol: str                          # "tcp", "udp", "icmp", "-1" (all)
    port_from: int | None = None
    port_to: int | None = None
    peer_cidr: str | None = None
    peer_group: str | None = None          # security-group reference
    action: str = "allow"
    priority: int | None = None


class MetricsSpec(BaseModel):
    """Observed utilisation metrics for a resource."""

    cpu_p50: float | None = None           # percent
    cpu_p95: float | None = None
    cpu_max: float | None = None
    mem_p95: float | None = None
    disk_iops_p95: float | None = None
    net_mbps_p95: float | None = None
    window_days: int | None = None
    metrics_source: str | None = None


class CostSpec(BaseModel):
    """Cost data for a resource."""

    actual_monthly_cost: float | None = None
    list_monthly_cost: float | None = None
    currency: str = "USD"
    price_catalog_version: str | None = None


# ---------------------------------------------------------------------------
# Base resource model
# ---------------------------------------------------------------------------


class Resource(BaseModel):
    """Base normalized cloud resource.

    All provider-specific resources inherit from this model. The ``spec``
    field on subclasses carries provider-independent structured data.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: ProviderName
    native_id: str                         # provider's own identifier
    account: str                           # AWS account ID / Azure subscription / GCP project
    region: str
    zone: str | None = None
    type: ResourceKind
    name: str | None = None
    status: ResourceStatus = ResourceStatus.unknown
    tags: dict[str, str] = Field(default_factory=dict)
    created_at_source: datetime | None = None
    snapshot_id: uuid.UUID | None = None
    discovered_at: datetime = Field(default_factory=_utcnow)
    schema_version: str = "1.0"
    raw_ref: str | None = None             # object-store key for raw API response


# ---------------------------------------------------------------------------
# VM specification
# ---------------------------------------------------------------------------


class VMSpec(Resource):
    """A virtual machine / compute instance resource.

    §10.2 — all compute-specific fields live here.
    """

    type: ResourceKind = ResourceKind.vm

    # Hardware profile
    vcpu: int | None = None
    memory_gib: float | None = None
    gpu_count: int | None = None
    gpu_model: str | None = None

    # Instance details
    instance_type: str | None = None       # native size (e.g. "m5.xlarge")
    architecture: str | None = None        # "x86_64" | "arm64"
    hypervisor: str | None = None

    # OS
    os_name: str | None = None
    os_version: str | None = None
    image_id: str | None = None

    # Networking
    nics: list[NicSpec] = Field(default_factory=list)
    primary_private_ip: str | None = None
    primary_public_ip: str | None = None

    # Storage
    disks: list[DiskSpec] = Field(default_factory=list)
    root_volume_size_gib: float | None = None

    # Licensing / tenancy
    license_model: str | None = None       # "BYOL", "Included", etc.
    tenancy: str | None = None             # "dedicated" | "shared" | "host"

    # Observed metrics (populated after discovery if metrics are available)
    metrics: MetricsSpec | None = None

    # Cost
    cost: CostSpec | None = None

    # Provenance per-field tracking (field_name → ProvenanceField)
    provenance: dict[str, ProvenanceField] = Field(default_factory=dict)

    # Extra provider-specific attributes not covered by the normalized model
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Graph edges and discovery snapshots
# ---------------------------------------------------------------------------


class ResourceEdge(BaseModel):
    """A directed relationship between two resources in the same workspace."""

    from_id: uuid.UUID
    to_id: uuid.UUID
    kind: EdgeKind
    workspace_id: uuid.UUID
    snapshot_id: uuid.UUID | None = None


class Snapshot(BaseModel):
    """A point-in-time discovery snapshot for a connection."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: ProviderName
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    status: str = "running"               # "running" | "completed" | "failed"
    coverage: dict[str, str] = Field(default_factory=dict)  # region → status


class NormalizedBundle(BaseModel):
    """A batch of normalized resources and their relationships.

    Returned by ``ProviderAdapter.normalize()`` and consumed by the ingestion
    pipeline to write into the DB.
    """

    resources: list[Resource] = Field(default_factory=list)
    edges: list[ResourceEdge] = Field(default_factory=list)
