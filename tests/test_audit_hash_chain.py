"""Tests that audit events form a valid SHA-256 hash chain.

The chain property: event_hash(n) = SHA-256(event_hash(n-1) + canonical_json(event_n))
Modifying any stored event must invalidate the chain from that point forward.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from audit.models import AuditEvent
from audit.writer import _ZERO_HASH, AuditWriter, compute_event_hash


def _make_event(**overrides: object) -> AuditEvent:
    """Construct a minimal AuditEvent for testing."""
    defaults = {
        "workspace_id": uuid.uuid4(),
        "actor_id": uuid.uuid4(),
        "actor_email": "test@example.com",
        "action": "test.action",
        "created_at": datetime(2025, 1, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    return AuditEvent(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_hash_chain_first_event_uses_zero_hash() -> None:
    """The first event must have prev_hash = all-zeros sentinel."""
    workspace_id = uuid.uuid4()
    writer = AuditWriter(workspace_id=workspace_id, session_factory=None)

    event = _make_event(workspace_id=workspace_id)
    recorded = await writer.record(event)

    assert recorded.prev_hash == _ZERO_HASH
    assert len(recorded.event_hash) == 64  # hex SHA-256


@pytest.mark.asyncio
async def test_hash_chain_second_event_references_first() -> None:
    """The second event's prev_hash must equal the first event's event_hash."""
    workspace_id = uuid.uuid4()
    writer = AuditWriter(workspace_id=workspace_id, session_factory=None)

    event1 = _make_event(workspace_id=workspace_id, action="first")
    event2 = _make_event(workspace_id=workspace_id, action="second")

    recorded1 = await writer.record(event1)
    recorded2 = await writer.record(event2)

    assert recorded2.prev_hash == recorded1.event_hash


@pytest.mark.asyncio
async def test_hash_chain_three_events() -> None:
    """Chain of 3 events: each event's prev_hash matches the previous event_hash."""
    workspace_id = uuid.uuid4()
    writer = AuditWriter(workspace_id=workspace_id, session_factory=None)

    events = [_make_event(workspace_id=workspace_id, action=f"action_{i}") for i in range(3)]
    recorded = [await writer.record(e) for e in events]

    # First must have zero hash
    assert recorded[0].prev_hash == _ZERO_HASH

    # Each subsequent event must chain from previous
    for i in range(1, 3):
        assert recorded[i].prev_hash == recorded[i - 1].event_hash, (
            f"Chain broken at index {i}: "
            f"prev_hash={recorded[i].prev_hash!r} != "
            f"event_hash[{i-1}]={recorded[i-1].event_hash!r}"
        )


def test_event_hash_is_deterministic() -> None:
    """compute_event_hash must return the same result for the same inputs."""
    event = _make_event(workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"))
    prev = "a" * 64

    hash1 = compute_event_hash(prev, event)
    hash2 = compute_event_hash(prev, event)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_different_events_produce_different_hashes() -> None:
    """Two events with different content must have different event_hashes."""
    event_a = _make_event(action="event.a")
    event_b = _make_event(action="event.b")
    prev = _ZERO_HASH

    hash_a = compute_event_hash(prev, event_a)
    hash_b = compute_event_hash(prev, event_b)

    assert hash_a != hash_b


def test_modifying_prev_hash_breaks_chain() -> None:
    """If we tamper with prev_hash, the recomputed hash will not match."""
    event = _make_event()
    original_prev = _ZERO_HASH
    tampered_prev = "b" * 64

    original_hash = compute_event_hash(original_prev, event)
    tampered_hash = compute_event_hash(tampered_prev, event)

    assert original_hash != tampered_hash
