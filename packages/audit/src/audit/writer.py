"""Audit writer — computes the hash chain and persists AuditEvents.

The writer fetches the ``event_hash`` of the last persisted row, then:
  1. Sets ``prev_hash`` on the new event.
  2. Computes ``event_hash = SHA-256(prev_hash + canonical_json(content_fields))``.
  3. Inserts the event via the provided DB session.

Thread-safety: the writer serialises writes through an ``asyncio.Lock`` so
that concurrent coroutines cannot race the "fetch last hash → insert" window.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from audit.models import AuditEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Sentinel for the first row in a workspace's audit log
_ZERO_HASH = "0" * 64


def _canonical_json(event: AuditEvent) -> str:
    """Return deterministic JSON of the event's content fields (no hash fields)."""
    data: dict[str, Any] = {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id),
        "actor_id": str(event.actor_id),
        "actor_email": event.actor_email,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "connection_id": str(event.connection_id) if event.connection_id else None,
        "tool_name": event.tool_name,
        "arguments_redacted": event.arguments_redacted,
        "result_status": event.result_status,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "created_at": event.created_at.isoformat(),
    }
    # sort_keys ensures determinism regardless of dict insertion order
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def compute_event_hash(prev_hash: str, event: AuditEvent) -> str:
    """Return the hex SHA-256 of ``prev_hash + canonical_json(event)``."""
    payload = prev_hash + _canonical_json(event)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditWriter:
    """Persists ``AuditEvent`` rows with an integrity hash chain.

    One instance should be shared per workspace (or per application).
    The optional ``session_factory`` argument accepts an async callable that
    returns an ``AsyncSession``; if omitted the writer operates in *dry-run*
    mode (hashes are computed but nothing is written to the DB — useful for
    testing hash-chain logic in isolation).
    """

    def __init__(
        self,
        workspace_id: uuid.UUID,
        session_factory: Any | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        # In-memory cache of the last event_hash — used in dry-run mode
        # (session_factory=None) and as a fast-path to avoid a DB round-trip.
        self._last_hash: str | None = None

    async def _fetch_last_hash(self, session: AsyncSession | None) -> str:
        """Return the ``event_hash`` of the most recent row for this workspace."""
        if session is None:
            # Dry-run mode: use in-memory cache
            return self._last_hash if self._last_hash is not None else _ZERO_HASH
        # Import here to avoid hard dependency when used without DB
        from sqlalchemy import text

        # Using raw SQL for simplicity; the ORM model lives in packages/db
        result = await session.execute(
            text(
                "SELECT event_hash FROM audit_events "
                "WHERE workspace_id = :wid "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"wid": str(self._workspace_id)},
        )
        row = result.fetchone()
        return row[0] if row else _ZERO_HASH

    async def _insert(self, session: AsyncSession | None, event: AuditEvent) -> None:
        """Write the event to the DB (no-op in dry-run mode)."""
        if session is None:
            return
        from sqlalchemy import text

        await session.execute(
            text(
                "INSERT INTO audit_events ("
                "  id, prev_hash, event_hash, workspace_id, actor_id, actor_email,"
                "  action, target_type, target_id, connection_id, tool_name,"
                "  arguments_redacted, result_status, request_id, trace_id, created_at"
                ") VALUES ("
                "  :id, :prev_hash, :event_hash, :workspace_id, :actor_id, :actor_email,"
                "  :action, :target_type, :target_id, :connection_id, :tool_name,"
                "  :arguments_redacted::jsonb, :result_status, :request_id, :trace_id, :created_at"
                ")"
            ),
            {
                "id": str(event.id),
                "prev_hash": event.prev_hash,
                "event_hash": event.event_hash,
                "workspace_id": str(event.workspace_id),
                "actor_id": str(event.actor_id),
                "actor_email": event.actor_email,
                "action": event.action,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "connection_id": str(event.connection_id) if event.connection_id else None,
                "tool_name": event.tool_name,
                "arguments_redacted": json.dumps(event.arguments_redacted),
                "result_status": event.result_status,
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "created_at": event.created_at,
            },
        )
        await session.commit()

    async def record(self, event: AuditEvent) -> AuditEvent:
        """Compute the hash chain fields and persist *event*.

        Returns the event with ``prev_hash`` and ``event_hash`` populated.
        The caller should use the returned object for any subsequent processing.
        """
        async with self._lock:
            session: AsyncSession | None = None
            if self._session_factory is not None:
                session = self._session_factory()

            try:
                prev_hash = await self._fetch_last_hash(session)
                event = event.model_copy(update={"prev_hash": prev_hash})
                event_hash = compute_event_hash(prev_hash, event)
                event = event.model_copy(update={"event_hash": event_hash})
                await self._insert(session, event)
                # Update in-memory cache after successful write
                self._last_hash = event_hash
            finally:
                if session is not None:
                    await session.close()

        return event
