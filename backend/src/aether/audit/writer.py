"""Hash-chained, append-only audit log.

Each event stores ``prev_hash`` (the hash of the previous event) and ``hash`` =
SHA-256 over the previous hash and a canonical JSON encoding of the event. Editing
or deleting any row breaks every later hash, which :func:`verify_chain` detects.

Writers take a transaction-scoped advisory lock so concurrent inserts are
serialised into one chain. Events are written in the caller's transaction, so an
action and its audit record commit (or roll back) together.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aether.core.enums import ActorType, AuditStatus
from aether.db.models import AuditEvent
from aether.logging import redact

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 0x0AE7_4E12  # arbitrary constant, shared by all writers


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: str | None
    display: str | None = None

    @classmethod
    def system(cls, name: str) -> Actor:
        return cls(ActorType.SYSTEM, name, name)

    @classmethod
    def service(cls, name: str) -> Actor:
        return cls(ActorType.SERVICE, name, name)


@dataclass(slots=True)
class AuditRecord:
    actor: Actor
    action: str
    status: AuditStatus = AuditStatus.SUCCESS
    workspace_id: uuid.UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    connection_id: uuid.UUID | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    trace_id: str | None = None


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_hash(prev_hash: str, fields: Mapping[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + b"|" + _canonical(fields)).hexdigest()


def _hashed_fields(ev: AuditEvent) -> dict[str, Any]:
    return {
        "id": str(ev.id),
        "occurred_at": ev.occurred_at.astimezone(UTC).isoformat(),
        "actor_type": ev.actor_type,
        "actor_id": ev.actor_id,
        "actor_display": ev.actor_display,
        "workspace_id": str(ev.workspace_id) if ev.workspace_id else None,
        "action": ev.action,
        "target_type": ev.target_type,
        "target_id": ev.target_id,
        "connection_id": str(ev.connection_id) if ev.connection_id else None,
        "status": ev.status,
        "details": ev.details,
        "request_id": ev.request_id,
        "trace_id": ev.trace_id,
    }


async def record(session: AsyncSession, rec: AuditRecord) -> AuditEvent:
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    prev = (
        await session.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))
    ).scalar_one_or_none() or GENESIS_HASH

    ev = AuditEvent(
        id=uuid.uuid4(),
        # Microsecond precision round-trips through Postgres timestamptz unchanged.
        occurred_at=datetime.now(UTC),
        actor_type=rec.actor.type.value,
        actor_id=rec.actor.id,
        actor_display=rec.actor.display,
        workspace_id=rec.workspace_id,
        action=rec.action,
        target_type=rec.target_type,
        target_id=rec.target_id,
        connection_id=rec.connection_id,
        status=rec.status.value,
        # Round-trip through JSON so the stored JSONB equals what we hashed.
        details=json.loads(_canonical(redact(dict(rec.details)))),
        request_id=rec.request_id,
        trace_id=rec.trace_id,
        prev_hash=prev,
    )
    ev.hash = compute_hash(prev, _hashed_fields(ev))
    session.add(ev)
    await session.flush()
    return ev


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None


async def verify_chain(session: AsyncSession, batch: int = 1000) -> ChainVerification:
    prev = GENESIS_HASH
    last_seq = 0
    checked = 0
    while True:
        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.seq > last_seq).order_by(AuditEvent.seq).limit(batch)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return ChainVerification(ok=True, checked=checked)
        for ev in rows:
            if ev.prev_hash != prev:
                return ChainVerification(False, checked, ev.seq, "prev_hash does not match previous event")
            if compute_hash(prev, _hashed_fields(ev)) != ev.hash:
                return ChainVerification(False, checked, ev.seq, "event content does not match its hash")
            prev = ev.hash
            last_seq = ev.seq
            checked += 1
