"""Unit tests for topology.graph — build_subgraph and generate_mermaid.

Uses an in-memory SQLite database with ResourceRow and ResourceEdgeRow records.
No HTTP layer, no real PostgreSQL.

Test cases:
    - build_subgraph returns correct node/edge counts at each depth
    - depth limit is respected
    - node count limit (max_nodes) sets truncated=True
    - direction='out' only follows outgoing edges
    - generate_mermaid output starts with 'flowchart'
    - generate_mermaid sanitizes special Mermaid characters
    - generate_mermaid truncates to 30 nodes and adds comment
"""

from __future__ import annotations

import uuid
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from topology.graph import TopologyNode, build_subgraph, generate_mermaid

# ---------------------------------------------------------------------------
# In-memory DB
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WS_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000011")
CONN_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000012")
SNAP_ID = uuid.UUID("cccc0000-0000-0000-0000-000000000013")


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
        from_id=frm.id,
        to_id=to.id,
        kind=kind,
        workspace_id=WS_ID,
        snapshot_id=SNAP_ID,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Shared rows — assigned after setup
vm: ResourceRow
nic: ResourceRow
subnet: ResourceRow
vpc: ResourceRow
sg: ResourceRow


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    global vm, nic, subnet, vpc, sg

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _session_factory() as session:
        ws = WorkspaceRow(id=WS_ID, name="Test", slug="test", settings={})
        session.add(ws)
        conn_row = ConnectionRow(
            id=CONN_ID, workspace_id=WS_ID, provider="aws",
            name="c", mode="read-only", metadata_={},
        )
        session.add(conn_row)
        snap = SnapshotRow(
            id=SNAP_ID, workspace_id=WS_ID, connection_id=CONN_ID,
            provider="aws", status="completed",
            coverage={}, completed_at=datetime.now(UTC),
        )
        session.add(snap)

        vm = _res("vm", "my-vm")
        nic = _res("nic", "my-nic")
        subnet = _res("subnet", "my-subnet")
        vpc = _res("network", "my-vpc")
        sg = _res("security_group", "my-sg")

        for r in (vm, nic, subnet, vpc, sg):
            session.add(r)

        # vm → nic (out)   nic → subnet (out)   subnet → vpc (out)   vm ← sg (sg protects vm)
        session.add(_edge(vm, nic, "attached_to"))
        session.add(_edge(nic, subnet, "in_subnet"))
        session.add(_edge(subnet, vpc, "routes_to"))
        session.add(_edge(sg, vm, "protected_by"))

        await session.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Tests — build_subgraph
# ---------------------------------------------------------------------------


class TestBuildSubgraph:

    async def _run(self, resource_id: uuid.UUID, depth: int, direction: str = "both",
                   max_nodes: int = 100):
        async with _session_factory() as session:
            return await build_subgraph(
                db=session,
                workspace_id=WS_ID,
                resource_id=resource_id,
                depth=depth,
                direction=direction,
                snapshot_id=SNAP_ID,
                max_nodes=max_nodes,
            )

    @pytest.mark.asyncio
    async def test_depth1_returns_root_and_direct_neighbours(self):
        nodes, edges, truncated = await self._run(vm.id, depth=1)
        node_ids = {n.id for n in nodes}
        assert str(vm.id) in node_ids
        assert str(nic.id) in node_ids
        # sg is in edges_in at depth 1 (both direction)
        assert str(sg.id) in node_ids
        assert not truncated

    @pytest.mark.asyncio
    async def test_depth2_includes_subnet_and_sg(self):
        nodes, edges, truncated = await self._run(vm.id, depth=2)
        node_ids = {n.id for n in nodes}
        assert str(subnet.id) in node_ids
        assert str(sg.id) in node_ids
        assert not truncated

    @pytest.mark.asyncio
    async def test_depth3_includes_vpc(self):
        nodes, edges, truncated = await self._run(vm.id, depth=3)
        node_ids = {n.id for n in nodes}
        assert str(vpc.id) in node_ids
        assert not truncated

    @pytest.mark.asyncio
    async def test_direction_out_skips_incoming_sg(self):
        """direction='out' should not include the SG (which has an edge *into* vm)."""
        nodes, edges, truncated = await self._run(vm.id, depth=1, direction="out")
        node_ids = {n.id for n in nodes}
        # nic should be present (outgoing edge from vm)
        assert str(nic.id) in node_ids
        # sg should NOT be present (it points to vm, not the other way)
        assert str(sg.id) not in node_ids

    @pytest.mark.asyncio
    async def test_max_nodes_sets_truncated(self):
        """max_nodes=1 means only the root fits → truncated=True."""
        nodes, edges, truncated = await self._run(vm.id, depth=3, max_nodes=1)
        assert truncated
        # Root must always be present
        assert any(n.is_root for n in nodes)

    @pytest.mark.asyncio
    async def test_edges_have_labels(self):
        nodes, edges, _ = await self._run(vm.id, depth=2)
        for e in edges:
            assert e.label  # all edges must have a non-empty label

    @pytest.mark.asyncio
    async def test_depth_limits_hop_count(self):
        """At depth=1 from nic we should reach vm and subnet but NOT vpc."""
        nodes, edges, _ = await self._run(nic.id, depth=1)
        node_ids = {n.id for n in nodes}
        assert str(vm.id) in node_ids
        assert str(subnet.id) in node_ids
        assert str(vpc.id) not in node_ids


# ---------------------------------------------------------------------------
# Tests — generate_mermaid
# ---------------------------------------------------------------------------

def _make_node(kind: str, name: str, is_root: bool = False) -> TopologyNode:
    nid = str(uuid.uuid4())
    return TopologyNode(
        id=nid, native_id=name, name=name, kind=kind,
        provider="aws", region="us-east-1", status="running",
        is_root=is_root, metadata={},
    )


class TestGenerateMermaid:

    def test_output_starts_with_flowchart(self):
        nodes = [_make_node("vm", "my-vm", is_root=True)]
        result = generate_mermaid(nodes, [])
        assert result.startswith("flowchart")

    def test_contains_node_label(self):
        nodes = [_make_node("vm", "web-server", is_root=True)]
        result = generate_mermaid(nodes, [])
        assert "web-server" in result

    def test_contains_edge_label(self):
        from topology.graph import TopologyEdge
        n1 = _make_node("vm", "vm1", is_root=True)
        n2 = _make_node("nic", "nic1")
        e = TopologyEdge(from_id=n1.id, to_id=n2.id, kind="attached_to", label="attached to")
        result = generate_mermaid([n1, n2], [e])
        assert "attached to" in result

    def test_sanitizes_special_characters(self):
        nodes = [_make_node("vm", 'vm[bad"name]', is_root=True)]
        result = generate_mermaid(nodes, [])
        # Original brackets and quotes must not appear
        assert "[bad" not in result
        assert '"name"' not in result

    def test_truncates_at_30_nodes(self):
        nodes = [_make_node("vm", f"vm-{i}", is_root=(i == 0)) for i in range(35)]
        result = generate_mermaid(nodes, [])
        # Comment about truncation
        assert "truncated" in result
        # Only 30 alias lines should appear (n + 8-char id)
        node_lines = [l for l in result.splitlines() if l.strip().startswith("n")]
        assert len(node_lines) == 30

    def test_root_node_present_after_truncation(self):
        nodes = [_make_node("vm", f"vm-{i}", is_root=(i == 0)) for i in range(35)]
        result = generate_mermaid(nodes, [])
        # Root (vm-0) label must be in the diagram
        assert "vm-0" in result

    def test_no_edges_for_excluded_nodes(self):
        """Edges referencing nodes that were truncated out must not appear."""
        from topology.graph import TopologyEdge
        nodes = [_make_node("vm", f"vm-{i}", is_root=(i == 0)) for i in range(35)]
        excluded_node = _make_node("disk", "orphan-disk")
        present_vm = nodes[0]
        e = TopologyEdge(
            from_id=present_vm.id,
            to_id=excluded_node.id,  # not in nodes list
            kind="attached_to",
            label="attached to",
        )
        result = generate_mermaid(nodes, [e])
        # The orphan-disk alias must not appear
        alias = "n" + excluded_node.id.replace("-", "")[:8]
        assert alias not in result
