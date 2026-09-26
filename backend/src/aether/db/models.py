from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, uuid.UUID: UUID(as_uuid=True)}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subject: Mapped[str] = mapped_column(String(255), unique=True)  # OIDC `sub`
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(back_populates="user", lazy="selectin")


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32))

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(lazy="joined")


class CloudConnection(TimestampMixin, Base):
    """Connection metadata. Secret material lives only in OpenBao at ``secret_path``."""

    __tablename__ = "cloud_connections"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    auth_method: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secret_path: Mapped[str | None] = mapped_column(String(255))
    secret_version: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AuditEvent(Base):
    """Append-only, hash-chained audit trail. UPDATE/DELETE are blocked by a trigger."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_workspace_seq", "workspace_id", "seq"),
        Index("ix_audit_occurred_at", "occurred_at"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(255))
    actor_display: Mapped[str | None] = mapped_column(String(320))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(255))
    connection_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)


class Snapshot(Base):
    """One discovery run of one connection. Immutable once finished."""

    __tablename__ = "snapshots"
    __table_args__ = (Index("ix_snapshots_ws_conn_started", "workspace_id", "connection_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cloud_connections.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # running | complete | partial | failed
    workflow_id: Mapped[str | None] = mapped_column(String(255))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    regions: Mapped[list[str] | None] = mapped_column(JSONB)
    coverage: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "native_id"),
        Index("ix_resources_ws_snapshot_type", "workspace_id", "snapshot_id", "type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cloud_connections.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(32))
    native_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    account: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64))
    zone: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    tags: Mapped[dict[str, Any]] = mapped_column(default=dict)
    spec: Mapped[dict[str, Any]] = mapped_column(default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    schema_version: Mapped[int] = mapped_column()
    # Denormalised VM attributes for filtering and sorting.
    vcpu: Mapped[int | None] = mapped_column()
    memory_mib: Mapped[int | None] = mapped_column()
    cpu_arch: Mapped[str | None] = mapped_column(String(16))
    os_family: Mapped[str | None] = mapped_column(String(16))
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceEdge(Base):
    __tablename__ = "resource_edges"
    __table_args__ = (Index("ix_edges_to", "to_id"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    from_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    to_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)


# --------------------------------------------------------------------------- catalog
# Global reference data (not workspace-scoped): instance specs, prices, disk tiers, FX.


class CatalogSync(Base):
    __tablename__ = "catalog_syncs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # running | complete | partial | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    regions: Mapped[list[str] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class CatalogInstanceSpec(Base):
    __tablename__ = "catalog_instance_specs"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    family: Mapped[str] = mapped_column(String(32))
    vcpu: Mapped[int] = mapped_column()
    memory_mib: Mapped[int] = mapped_column()
    cpu_arch: Mapped[str] = mapped_column(String(16))
    cpu_vendor: Mapped[str | None] = mapped_column(String(32))
    gpu_count: Mapped[int] = mapped_column(default=0)
    gpu_model: Mapped[str | None] = mapped_column(String(64))
    local_disk_gib: Mapped[int] = mapped_column(default=0)
    generation: Mapped[int] = mapped_column(default=0)
    spec_source: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogPrice(Base):
    __tablename__ = "catalog_prices"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    os: Mapped[str] = mapped_column(String(16), primary_key=True)
    model: Mapped[str] = mapped_column(String(16), primary_key=True)
    hourly_usd: Mapped[float] = mapped_column()
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(128))
    sync_id: Mapped[uuid.UUID | None] = mapped_column()
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogDiskPrice(Base):
    __tablename__ = "catalog_disk_prices"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), primary_key=True)
    disk_class: Mapped[str] = mapped_column(String(32), primary_key=True)
    tier: Mapped[str] = mapped_column(String(16), primary_key=True)  # "" for per-GiB classes
    size_gib: Mapped[int | None] = mapped_column()
    iops: Mapped[int | None] = mapped_column()
    monthly_usd: Mapped[float | None] = mapped_column()
    gib_month_usd: Mapped[float | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(128))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FxRateRow(Base):
    __tablename__ = "fx_rates"

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    per_usd: Mapped[float] = mapped_column()
    rate_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(128))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- assessment


class AssessmentRun(Base):
    __tablename__ = "assessment_runs"
    __table_args__ = (Index("ix_assessment_runs_ws_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    target_provider: Mapped[str] = mapped_column(String(16))
    target_region: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(32))
    ruleset_version: Mapped[str] = mapped_column(String(32))
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class FindingAcknowledgement(Base):
    """A reviewed, accepted warning. Keyed by the provider's native id so it survives new snapshots."""

    __tablename__ = "finding_acknowledgements"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    native_id: Mapped[str] = mapped_column(Text, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reason: Mapped[str] = mapped_column(Text)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_by_display: Mapped[str | None] = mapped_column(String(320))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- plans


class Plan(Base):
    """A versioned, content-hashed migration plan. Content is immutable (DB trigger);
    revisions are new rows sharing ``lineage_id``."""

    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version"),
        Index("ix_plans_ws_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    lineage_id: Mapped[uuid.UUID] = mapped_column()
    version: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))  # draft | in_review | approved | rejected | superseded
    content: Mapped[dict[str, Any]] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
    iac: Mapped[dict[str, Any]] = mapped_column()
    assessment_run_id: Mapped[uuid.UUID | None] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_display: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanReview(Base):
    """An approval or rejection, bound to the exact content hash that was reviewed."""

    __tablename__ = "plan_reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewer_display: Mapped[str | None] = mapped_column(String(320))
    decision: Mapped[str] = mapped_column(String(16))  # approve | reject
    comment: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
