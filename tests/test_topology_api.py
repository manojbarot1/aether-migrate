"""Integration tests for the topology API router.

Uses an in-memory SQLite database with the full resource graph seeded:
  VM → NIC → Subnet → VPC
  SG → VM  (protected_by)

Test cases:
    GET /topology/resources/{vm_id} depth=1 returns VM + NIC + SG
    depth=2 includes subnet
    depth=3 includes VPC
    direction=out only follows outgoing edges
    truncated=True when > 100 nodes (synthetic seed)
    Mermaid export contains node labels and edge labels
    Mermaid export sanitizes special characters in resource names
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

WS_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000021")
CONN_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000022")
SNAP_ID = uuid.UUID("cccc0000-0000-0000-0000-000000000023")

# IDs assigned after seeding
VM_ID: uuid.UUID
NIC_ID: uuid.UUID
SUBNET_ID: uuid.UUID
VPC_ID: uuid.UUID
SG_ID: uuid.UUID


def _res(kind: str, name: str) -> ResourceRow:
    return ResourceRow(
        id=uuid.uuid4(),
        workspace_id=WS_ID,
        connection_id=CONN_ID,
        snapshot_id=SNAP_ID,
        provider="aws",
        native_id=name,
        account="123",
        region="us-east-1",
        kind=kind,
        name=name,
        status="running",
        tags={},
        spec={},
        provenance={},
        schema_version="1.0",
    )


def _edge(frm: ResourceRow, to: ResourceRow, kind: str) -> ResourceEdgeRow:
    return ResourceEdgeRow(
        from_id=frm.id, to_id=to.id,
        kind=kind, workspace_id=WS_ID, snapshot_id=SNAP_ID,
    )


@pytest.fixture(scope="module", autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    global VM_ID, NIC_ID, SUBNET_ID, VPC_ID, SG_ID

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _session_factory() as session:
        ws = WorkspaceRow(id=WS_ID, name="T", slug="t", settings={})
        session.add(ws)
        c = ConnectionRow(id=CONN_ID, workspace_id=WS_ID, provider="aws",
                          name="c", mode="read-only", metadata_={})
        session.add(c)
        snap = SnapshotRow(id=SNAP_ID, workspace_id=WS_ID, connection_id=CONN_ID,
                           provider="aws", status="completed", coverage={},
                           completed_at=datetime.now(UTC))
        session.add(snap)

        vm = _res("vm", "my-vm")
        nic = _res("nic", "my-nic")
        subnet = _res("subnet", "my-subnet")
        vpc = _res("network", "my-vpc")
        sg = _res("security_group", "my-sg")

        for r in (vm, nic, subnet, vpc, sg):
            session.add(r)

        # VM → NIC → Subnet → VPC ;  SG → VM
        session.add(_edge(vm, nic, "attached_to"))
        session.add(_edge(nic, subnet, "in_subnet"))
        session.add(_edge(subnet, vpc, "routes_to"))
        session.add(_edge(sg, vm, "protected_by"))

        await session.commit()

        VM_ID = vm.id
        NIC_ID = nic.id
        SUBNET_ID = subnet.id
        VPC_ID = vpc.id
        SG_ID = sg.id

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


class TestTopologySubgraph:
    def setup_method(self) -> None:
        self.client = TestClient(_build_app(), raise_server_exceptions=True)

    def test_depth1_returns_vm_nic_sg(self) -> None:
        resp = self.client.get(f"/topology/resources/{VM_ID}?depth=1&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        data = resp.json()
        ids = {n["id"] for n in data["nodes"]}
        assert str(VM_ID) in ids
        assert str(NIC_ID) in ids
        # SG edges in to VM at depth 1 with direction=both
        assert str(SG_ID) in ids

    def test_depth2_includes_subnet(self) -> None:
        resp = self.client.get(f"/topology/resources/{VM_ID}?depth=2&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(SUBNET_ID) in ids

    def test_depth3_includes_vpc(self) -> None:
        resp = self.client.get(f"/topology/resources/{VM_ID}?depth=3&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(VPC_ID) in ids

    def test_direction_out_excludes_sg(self) -> None:
        """direction=out follows only outgoing edges; SG → VM is incoming to VM."""
        resp = self.client.get(
            f"/topology/resources/{VM_ID}?depth=1&direction=out&snapshot_id={SNAP_ID}"
        )
        assert resp.status_code == 200
        ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(NIC_ID) in ids
        assert str(SG_ID) not in ids

    def test_root_id_is_marked(self) -> None:
        resp = self.client.get(f"/topology/resources/{VM_ID}?depth=1&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        root_nodes = [n for n in resp.json()["nodes"] if n["is_root"]]
        assert len(root_nodes) == 1
        assert root_nodes[0]["id"] == str(VM_ID)

    def test_edges_have_label(self) -> None:
        resp = self.client.get(f"/topology/resources/{VM_ID}?depth=1&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        for edge in resp.json()["edges"]:
            assert edge["label"]

    def test_404_for_unknown_resource(self) -> None:
        fake = str(uuid.uuid4())
        resp = self.client.get(f"/topology/resources/{fake}?depth=1")
        assert resp.status_code == 404

    def test_invalid_direction_returns_422(self) -> None:
        resp = self.client.get(
            f"/topology/resources/{VM_ID}?depth=1&direction=sideways&snapshot_id={SNAP_ID}"
        )
        assert resp.status_code == 422

    def test_truncated_flag_with_small_max_nodes(self) -> None:
        """Seed many resources to trigger truncation via the build_subgraph max_nodes limit.

        We verify the flag by patching max_nodes=1 via the topology package directly.
        """
        import asyncio

        from topology.graph import build_subgraph

        async def _run():
            async with _session_factory() as session:
                _, _, truncated = await build_subgraph(
                    db=session,
                    workspace_id=WS_ID,
                    resource_id=VM_ID,
                    depth=3,
                    direction="both",
                    snapshot_id=SNAP_ID,
                    max_nodes=1,
                )
            return truncated

        truncated = asyncio.get_event_loop().run_until_complete(_run())
        assert truncated is True


class TestMermaidExport:
    def setup_method(self) -> None:
        self.client = TestClient(_build_app(), raise_server_exceptions=True)

    def test_returns_text_starting_with_flowchart(self) -> None:
        resp = self.client.get(f"/topology/export/{VM_ID}?depth=2&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        assert resp.text.strip().startswith("flowchart") or resp.text.strip().startswith("%%")

    def test_contains_node_names(self) -> None:
        resp = self.client.get(f"/topology/export/{VM_ID}?depth=2&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        assert "my-vm" in resp.text

    def test_contains_edge_label(self) -> None:
        resp = self.client.get(f"/topology/export/{VM_ID}?depth=1&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        # "attached to" is the label for the vm→nic edge
        assert "attached to" in resp.text

    def test_sanitizes_special_characters(self) -> None:
        """Resource with special chars in name should not cause raw injection in Mermaid."""
        import asyncio

        async def _seed_bad():
            async with _session_factory() as session:
                bad = ResourceRow(
                    id=uuid.uuid4(),
                    workspace_id=WS_ID,
                    connection_id=CONN_ID,
                    snapshot_id=SNAP_ID,
                    provider="aws",
                    native_id='bad"name[vm]',
                    account="123",
                    region="us-east-1",
                    kind="vm",
                    name='bad"name[vm]',
                    status="running",
                    tags={},
                    spec={},
                    provenance={},
                    schema_version="1.0",
                )
                session.add(bad)
                e = ResourceEdgeRow(
                    from_id=VM_ID, to_id=bad.id,
                    kind="depends_on", workspace_id=WS_ID, snapshot_id=SNAP_ID,
                )
                session.add(e)
                await session.commit()
                return bad.id

        bad_id = asyncio.get_event_loop().run_until_complete(_seed_bad())
        resp = self.client.get(f"/topology/export/{VM_ID}?depth=1&snapshot_id={SNAP_ID}")
        assert resp.status_code == 200
        # Raw unsafe chars must not appear literally inside a node label
        assert '["bad"name[vm]"]' not in resp.text

    def test_unsupported_format_returns_422(self) -> None:
        resp = self.client.get(
            f"/topology/export/{VM_ID}?format=svg&snapshot_id={SNAP_ID}"
        )
        assert resp.status_code == 422
