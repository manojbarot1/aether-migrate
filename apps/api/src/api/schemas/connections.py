"""Pydantic request / response schemas for the connections API.

Security rules enforced here:
- ``ConnectionCreateRequest`` accepts credential fields for validation only.
- ``ConnectionResponse`` NEVER includes any credential field.
- ``ConnectionTestResult`` carries only identity ARN and permission warnings.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from core.models import ProviderName
from pydantic import BaseModel, Field, model_validator


class ConnectionCreateRequest(BaseModel):
    """Request body for ``POST /connections``."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    provider: ProviderName
    mode: Literal["read-only", "execute"] = "read-only"

    # AWS credential block — stored in OpenBao, never in Postgres
    aws_role_arn: str | None = None
    aws_external_id: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_default_region: str = "us-east-1"

    scope: str | None = None  # e.g. "account/123456789"

    @model_validator(mode="after")
    def external_id_required_with_role_arn(self) -> ConnectionCreateRequest:
        """Enforce that external_id is set whenever role_arn is provided."""
        if self.aws_role_arn and not self.aws_external_id:
            raise ValueError("aws_external_id is required when aws_role_arn is set")
        return self


class ConnectionResponse(BaseModel):
    """Response schema for connection metadata.

    Credential fields are intentionally absent — secrets live only in OpenBao.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    provider: ProviderName
    mode: str
    scope: str | None
    last_tested_at: datetime | None
    last_test_ok: bool | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConnectionTestResult(BaseModel):
    """Result of running ``POST /connections/{id}/test``."""

    ok: bool
    identity: str | None = None        # e.g. "arn:aws:iam::123456789:assumed-role/..."
    warnings: list[str] = []           # excess-permission warnings
    errors: list[str] = []
