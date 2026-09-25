"""Audit event model with hash-chain support.

Each ``AuditEvent`` row carries:
- ``prev_hash``  — SHA-256 of the previous row's ``event_hash`` (or all-zeros for row #1)
- ``event_hash`` — SHA-256 of ``prev_hash`` concatenated with the canonical JSON of the
                   event's content fields (everything except the two hash fields).

This lets any verifier reproduce the chain without trusting the application.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AuditEvent(BaseModel):
    """A single entry in the append-only audit log."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)

    # Hash chain — populated by AuditWriter before persistence
    prev_hash: str = ""   # hex SHA-256 of the previous event_hash
    event_hash: str = ""  # hex SHA-256 of (prev_hash + canonical_json(content))

    # Actor
    workspace_id: uuid.UUID
    actor_id: uuid.UUID
    actor_email: str

    # Action description
    action: str                           # e.g. "connection.create"
    target_type: str | None = None        # e.g. "Connection"
    target_id: str | None = None          # target resource ID
    connection_id: uuid.UUID | None = None

    # Tool invocation details (for AI/MCP calls)
    tool_name: str | None = None
    arguments_redacted: dict[str, Any] = Field(default_factory=dict)

    # Outcome
    result_status: str = "success"        # "success" | "failure"

    # Tracing
    request_id: str | None = None
    trace_id: str | None = None

    # Timestamp
    created_at: datetime = Field(default_factory=_utcnow)
