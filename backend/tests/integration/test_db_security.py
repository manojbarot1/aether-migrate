"""Database-level guarantees: audit immutability/tamper detection and RLS isolation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from aether.audit.writer import Actor, AuditRecord, record, verify_chain
from aether.db.models import CloudConnection, Workspace
from aether.db.session import session_scope, workspace_scope

from .conftest import exec_sql

pytestmark = pytest.mark.integration


async def _new_workspace() -> uuid.UUID:
    ws_id = uuid.uuid4()
    async with session_scope() as s:
        s.add(Workspace(id=ws_id, slug=f"t-{ws_id.hex[:10]}", name="t", settings={}))
    return ws_id


async def test_audit_chain_verifies() -> None:
    async with session_scope() as s:
        for i in range(3):
            await record(s, AuditRecord(actor=Actor.system("test"), action="test.event", details={"i": i}))
    async with session_scope() as s:
        v = await verify_chain(s)
    assert v.ok, v
    assert v.checked >= 3


async def test_app_role_cannot_modify_audit() -> None:
    async with session_scope() as s:
        await record(s, AuditRecord(actor=Actor.system("test"), action="test.immutable"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with session_scope() as s:
            await s.execute(text("UPDATE audit_events SET action = 'x'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with session_scope() as s:
            await s.execute(text("DELETE FROM audit_events"))


async def test_owner_is_blocked_by_trigger(owner_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="append-only"):
        await exec_sql(
            owner_engine,
            "UPDATE audit_events SET action = 'x' WHERE seq = (SELECT max(seq) FROM audit_events)",
        )
    with pytest.raises(DBAPIError, match="append-only"):
        await exec_sql(owner_engine, "TRUNCATE audit_events")


async def test_tampering_is_detected(owner_engine: AsyncEngine) -> None:
    async with session_scope() as s:
        ev = await record(
            s, AuditRecord(actor=Actor.system("test"), action="test.tamper", details={"amount": 1})
        )
        seq = ev.seq
    # An attacker with owner rights disables the trigger and edits history...
    async with owner_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete"))
        await conn.execute(
            text("UPDATE audit_events SET details = '{\"amount\": 999}'::jsonb WHERE seq = :s"), {"s": seq}
        )
        await conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update_delete"))
    async with session_scope() as s:
        v = await verify_chain(s)
    assert not v.ok
    assert v.first_bad_seq == seq

    # Restore so later tests see a valid chain.
    async with owner_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete"))
        await conn.execute(
            text("UPDATE audit_events SET details = '{\"amount\": 1}'::jsonb WHERE seq = :s"), {"s": seq}
        )
        await conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update_delete"))
    async with session_scope() as s:
        assert (await verify_chain(s)).ok


async def test_rls_isolates_workspaces() -> None:
    ws_a, ws_b = await _new_workspace(), await _new_workspace()
    conn_id = uuid.uuid4()
    async with workspace_scope(ws_a) as s:
        s.add(
            CloudConnection(
                id=conn_id,
                workspace_id=ws_a,
                name="a",
                provider="aws",
                mode="read_only",
                auth_method="aws_access_key",
                config={},
                status="untested",
            )
        )

    async with workspace_scope(ws_b) as s:
        # Even an unfiltered query cannot see workspace A's rows.
        assert (await s.execute(select(CloudConnection))).scalars().all() == []
    async with session_scope() as s:
        # No workspace set at all: nothing is visible.
        assert (await s.execute(select(CloudConnection))).scalars().all() == []
    async with workspace_scope(ws_a) as s:
        assert [c.id for c in (await s.execute(select(CloudConnection))).scalars()] == [conn_id]

    # Writing a row into another workspace is rejected by the WITH CHECK clause.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with workspace_scope(ws_b) as s:
            s.add(
                CloudConnection(
                    id=uuid.uuid4(),
                    workspace_id=ws_a,
                    name="sneaky",
                    provider="aws",
                    mode="read_only",
                    auth_method="aws_access_key",
                    config={},
                    status="untested",
                )
            )
