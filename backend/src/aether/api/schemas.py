from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aether.core.connections import AwsAccessKeySecret, ConnectionConfig
from aether.core.enums import ConnectionMode, Provider, Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceOut(ORM):
    id: uuid.UUID
    slug: str
    name: str
    settings: dict[str, Any]
    created_at: datetime


class MembershipOut(BaseModel):
    workspace: WorkspaceOut
    role: Role


class MeOut(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str | None
    is_platform_admin: bool
    memberships: list[MembershipOut]


class WorkspaceCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    name: str = Field(min_length=1, max_length=255)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    role: Role


class MemberUpsert(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    provider: Provider
    mode: ConnectionMode = ConnectionMode.READ_ONLY
    config: ConnectionConfig
    # Write-only. Present only for key-based auth methods; never returned.
    secret: AwsAccessKeySecret | None = None


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    regions: list[str] | None = None
    secret: AwsAccessKeySecret | None = None  # rotate


class ConnectionOut(ORM):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    provider: Provider
    mode: ConnectionMode
    auth_method: str
    config: dict[str, Any]
    has_secret: bool
    secret_version: int | None
    status: str
    last_tested_at: datetime | None
    last_test_result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class AuditEventOut(ORM):
    seq: int
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    actor_display: str | None
    workspace_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    connection_id: uuid.UUID | None
    status: str
    details: dict[str, Any]
    request_id: str | None
    hash: str


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_before: int | None


class ChainVerificationOut(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None
    reason: str | None


class ClientConfig(BaseModel):
    oidc_authority: str
    oidc_client_id: str
    version: str
    env: str


class PolicyTemplate(BaseModel):
    provider: Provider
    auth_method: str
    external_id: str | None
    trust_policy: dict[str, Any] | None
    permissions_policy: dict[str, Any]
    instructions: list[str]
