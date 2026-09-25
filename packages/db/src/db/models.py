"""SQLAlchemy ORM models for AETHER MIGRATE.

All tables include a ``workspace_id`` column so that PostgreSQL Row-Level
Security policies can restrict data access to the authenticated workspace.

Column naming follows PostgreSQL conventions (snake_case). JSONB is used for
structured data that does not require relational querying (settings, spec, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    users: Mapped[list[UserRow]] = relationship("UserRow", back_populates="workspace")
    connections: Mapped[list[ConnectionRow]] = relationship(
        "ConnectionRow", back_populates="workspace"
    )


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


_USER_ROLES = ("viewer", "analyst", "connection-admin", "approver", "operator", "admin")


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(
        Enum(*_USER_ROLES, name="user_role"), nullable=False, default="viewer"
    )
    oidc_subject: Mapped[str | None] = mapped_column(String(512), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workspace: Mapped[WorkspaceRow] = relationship("WorkspaceRow", back_populates="users")
    api_tokens: Mapped[list[ApiTokenRow]] = relationship("ApiTokenRow", back_populates="user")

    __table_args__ = (
        UniqueConstraint("workspace_id", "email", name="uq_users_workspace_email"),
        Index("ix_users_workspace_id", "workspace_id"),
    )


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


_PROVIDER_NAMES = ("aws", "azure", "gcp", "ibm")
_CONNECTION_MODES = ("read-only", "execute")


class ConnectionRow(Base):
    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        Enum(*_PROVIDER_NAMES, name="provider_name"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(
        Enum(*_CONNECTION_MODES, name="connection_mode"), nullable=False, default="read-only"
    )
    scope: Mapped[str | None] = mapped_column(Text)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    # Masked metadata only — NO secrets stored here. Secrets live in OpenBao.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[WorkspaceRow] = relationship("WorkspaceRow", back_populates="connections")
    snapshots: Mapped[list[SnapshotRow]] = relationship("SnapshotRow", back_populates="connection")

    __table_args__ = (Index("ix_connections_workspace_id", "workspace_id"),)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        Enum(*_PROVIDER_NAMES, name="provider_name"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    connection: Mapped[ConnectionRow] = relationship("ConnectionRow", back_populates="snapshots")
    resources: Mapped[list[ResourceRow]] = relationship("ResourceRow", back_populates="snapshot")

    __table_args__ = (
        Index("ix_snapshots_workspace_id", "workspace_id"),
        Index("ix_snapshots_connection_id", "connection_id"),
    )


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


class ResourceRow(Base):
    __tablename__ = "resources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(
        Enum(*_PROVIDER_NAMES, name="provider_name"), nullable=False
    )
    native_id: Mapped[str] = mapped_column(String(512), nullable=False)
    account: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False)
    zone: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_ref: Mapped[str | None] = mapped_column(Text)
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")

    snapshot: Mapped[SnapshotRow | None] = relationship("SnapshotRow", back_populates="resources")

    __table_args__ = (
        Index("ix_resources_workspace_id", "workspace_id"),
        Index("ix_resources_snapshot_id", "snapshot_id"),
        Index("ix_resources_provider_region", "provider", "region"),
        Index("ix_resources_kind", "kind"),
        UniqueConstraint(
            "workspace_id", "connection_id", "snapshot_id", "native_id",
            name="uq_resources_native_id"
        ),
    )


# ---------------------------------------------------------------------------
# Resource Edge
# ---------------------------------------------------------------------------


class ResourceEdgeRow(Base):
    __tablename__ = "resource_edges"

    from_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    to_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind: Mapped[str] = mapped_column(String(50), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_resource_edges_workspace_id", "workspace_id"),)


# ---------------------------------------------------------------------------
# Audit Event
# ---------------------------------------------------------------------------


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(100))
    target_id: Mapped[str | None] = mapped_column(String(512))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tool_name: Mapped[str | None] = mapped_column(String(100))
    arguments_redacted: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_status: Mapped[str] = mapped_column(String(50), nullable=False, default="success")
    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_events_workspace_id", "workspace_id"),
        Index("ix_audit_events_actor_id", "actor_id"),
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_action", "action"),
    )


# ---------------------------------------------------------------------------
# API Token
# ---------------------------------------------------------------------------


class ApiTokenRow(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[UserRow] = relationship("UserRow", back_populates="api_tokens")

    __table_args__ = (
        Index("ix_api_tokens_workspace_id", "workspace_id"),
        Index("ix_api_tokens_token_hash", "token_hash"),
    )


# ---------------------------------------------------------------------------
# AI Conversations
# ---------------------------------------------------------------------------


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list[MessageRow]] = relationship(
        "MessageRow", back_populates="conversation", order_by="MessageRow.created_at"
    )
    tool_results: Mapped[list[ToolResultRow]] = relationship(
        "ToolResultRow", back_populates="conversation"
    )

    __table_args__ = (
        Index("ix_conversations_workspace_id", "workspace_id"),
        Index("ix_conversations_user_id", "user_id"),
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user|assistant|tool
    content: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(100))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    tool_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_results.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[ConversationRow] = relationship(
        "ConversationRow", back_populates="messages"
    )

    __table_args__ = (Index("ix_messages_conversation_id", "conversation_id"),)


class ToolResultRow(Base):
    __tablename__ = "tool_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of input
    result_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="SET NULL")
    )
    result_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[ConversationRow] = relationship(
        "ConversationRow", back_populates="tool_results"
    )

    __table_args__ = (Index("ix_tool_results_conversation_id", "conversation_id"),)


# ---------------------------------------------------------------------------
# Catalog — Instance Types
# ---------------------------------------------------------------------------


class CatalogInstanceTypeRow(Base):
    __tablename__ = "catalog_instance_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_mib: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_arch: Mapped[str] = mapped_column(Text, nullable=False)
    cpu_vendor: Mapped[str | None] = mapped_column(Text)
    gpu_model: Mapped[str | None] = mapped_column(Text)
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    local_nvme_gib: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    network_bandwidth_gbps: Mapped[float | None] = mapped_column(Float)
    os_support: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    available_in_region: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider", "region", "sku", "catalog_version",
                         name="uq_catalog_instance_types"),
        Index("ix_catalog_instance_types_provider_region", "provider", "region"),
        Index("ix_catalog_instance_types_sku", "sku"),
    )


# ---------------------------------------------------------------------------
# Catalog — Prices
# ---------------------------------------------------------------------------


class CatalogPriceRow(Base):
    __tablename__ = "catalog_prices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    os_type: Mapped[str] = mapped_column(Text, nullable=False)
    license_model: Mapped[str] = mapped_column(Text, nullable=False)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    price_usd: Mapped[Any] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="USD")
    price_per: Mapped[str] = mapped_column(Text, nullable=False, default="hour")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "region", "sku", "os_type", "license_model", "term", "catalog_version",
            name="uq_catalog_prices",
        ),
        Index("ix_catalog_prices_provider_region_sku", "provider", "region", "sku"),
    )


# ---------------------------------------------------------------------------
# FX Rates
# ---------------------------------------------------------------------------


class FXRateRow(Base):
    __tablename__ = "fx_rates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False, default="USD")
    target_currency: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Any] = mapped_column(Numeric(12, 6), nullable=False)
    rate_date: Mapped[Any] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ecb")

    __table_args__ = (
        UniqueConstraint("base_currency", "target_currency", "rate_date", name="uq_fx_rates"),
        Index("ix_fx_rates_target_currency_date", "target_currency", "rate_date"),
    )


# ---------------------------------------------------------------------------
# Catalog — Disk Prices
# ---------------------------------------------------------------------------


class CatalogDiskPriceRow(Base):
    __tablename__ = "catalog_disk_prices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    disk_type: Mapped[str] = mapped_column(Text, nullable=False)
    price_per_gib_month: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    price_per_iops_month: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    price_per_mbps_month: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    max_size_gib: Mapped[int | None] = mapped_column(Integer)
    max_iops: Mapped[int | None] = mapped_column(Integer)
    max_throughput_mbps: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_catalog_disk_prices_provider_region", "provider", "region"),
        Index("ix_catalog_disk_prices_disk_type", "disk_type"),
    )


# ---------------------------------------------------------------------------
# Assessment Results
# ---------------------------------------------------------------------------


class AssessmentResultRow(Base):
    __tablename__ = "assessment_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="SET NULL")
    )
    target_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    target_region: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="completed")
    readiness: Mapped[str] = mapped_column(String(50), nullable=False)
    readiness_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocker_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_assessment_results_workspace_id", "workspace_id"),
        Index("ix_assessment_results_resource_id", "resource_id"),
        Index("ix_assessment_results_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Finding Acknowledgements
# ---------------------------------------------------------------------------


class FindingAcknowledgementRow(Base):
    __tablename__ = "finding_acknowledgements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False)
    acknowledged_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "resource_id", "rule_id",
                          name="uq_finding_acknowledgements"),
        Index("ix_finding_acknowledgements_workspace_resource", "workspace_id", "resource_id"),
    )


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

_PLAN_STATUSES = ("draft", "in_review", "approved", "superseded", "executed")


class PlanRow(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum(*_PLAN_STATUSES, name="plan_status"), nullable=False, default="draft"
    )
    # Scope
    source_resource_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    target_region: Mapped[str] = mapped_column(String(100), nullable=False)
    sizing_strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    # Immutable content references
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Plan document (full serialized MigrationPlan)
    plan_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # AI narrative (stored separately from the plan document)
    ai_narrative: Mapped[str | None] = mapped_column(Text)
    ai_narrative_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_narrative_model: Mapped[str | None] = mapped_column(String(100))
    # Approval
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Versioning
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="SET NULL")
    )
    # Ownership
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_plans_workspace_id", "workspace_id"),
        Index("ix_plans_status", "status"),
        Index("ix_plans_created_at", "created_at"),
        Index("ix_plans_snapshot_id", "snapshot_id"),
    )


class PlanApprovalRow(Base):
    __tablename__ = "plan_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # "approved" | "rejected"
    note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_plan_approvals_plan_id", "plan_id"),
        Index("ix_plan_approvals_workspace_id", "workspace_id"),
    )
