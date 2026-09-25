"""Integration tests for the topology diff API endpoint.

Seed:
    Snapshot A: VM, NIC, Subnet, SG; edges: VM→NIC, NIC→Subnet, SG→VM
    Snapshot B: VM, NIC, Subnet, new Disk; edges: VM→NIC, NIC→Subnet, VM→Disk
               (SG removed, Disk added)

Test cases:
    added_nodes contains the new Disk
    removed_nodes contains the removed SG
    changed_nodes contains VM if its spec changed between snapshots
    unchanged nodes are not in any diff list
    added_edges / removed_edges reflect edge changes
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from db.models import (
    Base,
    ConnectionRow,
    ResourceEdgeRow,
    ResourceRow,
    SnapshotRow,
    WorkspaceRow,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# In-memory DB
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WS_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000031")
CONN_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000032")
SNAP_ID_A = uuid.UUID("cccc0000-0000-0000-0000-000000000033")
SNAP_ID_B = uuid.UUID("dddd0000-0000-0000-0000-000000000034")


def _res(kind: str, native_id: str, snap_id: uuid.UUID, spec=None) -> ResourceRow:
    return ResourceRow(
        id=uuid.uuid4(),
        workspace_id=WS_ID,
        connection_id=CONN_ID,
        snapshot_id=snap_id,
        provider="aws",
        native_id=native_id,
        account="123",
        region="us-east-1",
        kind=kind,
        name=native_id,
        status="running",
        tags={},
        spec=spec or {},
        provenance={},
        schema_version="1.0",
    )


def _edge(frm: ResourceRow, to: ResourceRow, kind: str, snap_id: uuid.UUID) -> ResourceEdgeRow:
    return ResourceEdgeRow(
        from_id=frm.id, to_id=to.id,
        kind=kind, workspace_id=WS_ID, snapshot_id=snap_id,
    )


# native IDs (stable across snapshots so the diff can match them)
NAT_VM = "nat-vm-001"
NAT_NIC = "nat-nic-001"
NAT_SUBNET = "nat-subnet-001"
NAT_SG = "nat-sg-001"       # present in A only
NAT_DISK = "nat-disk-001"   # present in B only
NAT_VM_CHANGED = "nat-vm-changed"  # present in both, spec changes


@pytest.fixture(scope="module", autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _session_factory() as session:
        ws = WorkspaceRow(id=WS_ID, name="T", slug="td", settings={})
        session.add(ws)
        c = ConnectionRow(id=CONN_ID, workspace_id=WS_ID, provider="aws",
                          name="c", mode="read-only", metadata_={})
        session.add(c)
        for sid in (SNAP_ID_A, SNAP_ID_B):
            session.add(SnapshotRow(
                id=sid, workspace_id=WS_ID, connection_id=CONN_ID,
                provider="aws", status="completed", coverage={},
                completed_at=datetime.now(UTC),
            ))

        # ── Snapshot A ──
        vm_a = _res("vm", NAT_VM, SNAP_ID_A, spec={"vcpu": 4})
        nic_a = _res("nic", NAT_NIC, SNAP_ID_A)
        subnet_a = _res("subnet", NAT_SUBNET, SNAP_ID_A)
        sg_a = _res("security_group", NAT_SG, SNAP_ID_A)
        vm_changed_a = _res("vm", NAT_VM_CHANGED, SNAP_ID_A, spec={"vcpu": 2})

        for r in (vm_a, nic_a, subnet_a, sg_a, vm_changed_a):
            session.add(r)
        session.add(_edge(vm_a, nic_a, "attached_to", SNAP_ID_A))
        session.add(_edge(nic_a, subnet_a, "in_subnet", SNAP_ID_A))
        session.add(_edge(sg_a, vm_a, "protected_by", SNAP_ID_A))

        # ── Snapshot B ──
        vm_b = _res("vm", NAT_VM, SNAP_ID_B, spec={"vcpu": 4})       # unchanged
        nic_b = _res("nic", NAT_NIC, SNAP_ID_B)
        subnet_b = _res("subnet", NAT_SUBNET, SNAP_ID_B)
        disk_b = _res("disk", NAT_DISK, SNAP_ID_B)                    # added
        vm_changed_b = _res("vm", NAT_VM_CHANGED, SNAP_ID_B, spec={"vcpu": 8})  # spec changed

        for r in (vm_b, nic_b, subnet_b, disk_b, vm_changed_b):
            session.add(r)
        session.add(_edge(vm_b, nic_b, "attached_to", SNAP_ID_B))
        session.add(_edge(nic_b, subnet_b, "in_subnet", SNAP_ID_B))
        session.add(_edge(vm_b, disk_b, "attached_to", SNAP_ID_B))   # new edge

        await session.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# FastAPI test app
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    from api.auth import AuthenticatedUser, get_current_user
    from api.dependencies import get_db_session, get_workspace_id
    from api.routers import topology

    app = FastAPI()

    mock_user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="test@example.com",
        workspace_id=WS_ID,
        roles=["analyst"],
        token_kind="api_token",
    )

    async def _mock_db() -> AsyncGenerator[AsyncSession, None]:
        async with _session_factory() as session:
            yield session

    async def _mock_user() -> AuthenticatedUser:
        return mock_user

    async def _mock_ws() -> uuid.UUID:
        return WS_ID

    app.dependency_overrides[get_db_session] = _mock_db
    app.dependency_overrides[get_current_user] = _mock_user
    app.dependency_overrides[get_workspace_id] = _mock_ws
    app.include_router(topology.router)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTopologyDiff:
    def setup_method(self) -> None:
        self.client = TestClient(_build_app(), raise_server_exceptions=True)

    def _diff(self):
        resp = self.client.get(f"/topology/diff/{SNAP_ID_A}/{SNAP_ID_B}")
        assert resp.status_code == 200
        return resp.json()

    def test_added_nodes_contains_disk(self) -> None:
        data = self._diff()
        added_native_ids = {n["native_id"] for n in data["added_nodes"]}
        assert NAT_DISK in added_native_ids

    def test_removed_nodes_contains_sg(self) -> None:
        data = self._diff()
        removed_native_ids = {n["native_id"] for n in data["removed_nodes"]}
        assert NAT_SG in removed_native_ids

    def test_unchanged_nodes_not_in_diff(self) -> None:
        data = self._diff()
        added = {n["native_id"] for n in data["added_nodes"]}
        removed = {n["native_id"] for n in data["removed_nodes"]}
        changed = {n["native_id"] for n in data["changed_nodes"]}
        # VM and NIC and Subnet are unchanged
        for unchanged_id in (NAT_VM, NAT_NIC, NAT_SUBNET):
            assert unchanged_id not in added
            assert unchanged_id not in removed

    def test_changed_nodes_contains_vm_changed(self) -> None:
        data = self._diff()
        changed_native_ids = {n["native_id"] for n in data["changed_nodes"]}
        assert NAT_VM_CHANGED in changed_native_ids

    def test_added_edges_contains_vm_to_disk(self) -> None:
        data = self._diff()
        # In B, VM→Disk edge was added (by native_id)
        added_edge_pairs = {(e["from_id"], e["to_id"]) for e in data["added_edges"]}
        # from_id and to_id are native_ids in the diff response
        assert any(t == NAT_DISK for _, t in added_edge_pairs)

    def test_removed_edges_contains_sg_to_vm(self) -> None:
        data = self._diff()
        removed_edge_pairs = {(e["from_id"], e["to_id"]) for e in data["removed_edges"]}
        # SG→VM edge was in A; not in B
        assert any(f == NAT_SG for f, _ in removed_edge_pairs)

    def test_diff_of_same_snapshot_is_empty(self) -> None:
        resp = self.client.get(f"/topology/diff/{SNAP_ID_A}/{SNAP_ID_A}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["added_nodes"] == []
        assert data["removed_nodes"] == []
        assert data["changed_nodes"] == []
        assert data["added_edges"] == []
        assert data["removed_edges"] == []
