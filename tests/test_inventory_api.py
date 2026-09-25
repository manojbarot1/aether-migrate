"""Integration tests for the inventory API.

Uses an in-memory SQLite database seeded with ResourceRow records.
No real PostgreSQL, no network, no Temporal required.

Test cases:
- GET /inventory/vms returns seeded VMs
- GET /inventory/vms filters by min_vcpu
- GET /inventory/vms returns snapshot_time in response
- GET /inventory/resources/{id} returns full detail
- GET /inventory/vms defaults to latest snapshot
- GET /inventory/diff compares two snapshots
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from db.models import Base, ConnectionRow, ResourceRow, SnapshotRow, WorkspaceRow
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# In-memory DB setup
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000002")
SNAPSHOT_ID_1 = uuid.UUID("cccc0000-0000-0000-0000-000000000003")
SNAPSHOT_ID_2 = uuid.UUID("dddd0000-0000-0000-0000-000000000004")


async def _create_tables() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop_tables() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------


def _make_resource(
    native_id: str,
    snapshot_id: uuid.UUID,
    kind: str = "vm",
    status: str = "running",
    vcpu: int = 4,
    memory_gib: float = 16.0,
    region: str = "us-east-1",
    name: str | None = None,
) -> ResourceRow:
    return ResourceRow(
        id=uuid.uuid4(),
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        snapshot_id=snapshot_id,
        provider="aws",
        native_id=native_id,
        account="123456789012",
        region=region,
        zone="us-east-1a",
        kind=kind,
        name=name or native_id,
        status=status,
        tags={},
        spec={"vcpu": vcpu, "memory_gib": memory_gib, "os_name": "linux"},
        provenance={},
        raw_ref=None,
        schema_version="1.0",
    )


def _make_snapshot(
    snap_id: uuid.UUID,
    status: str = "completed",
    completed_at: datetime | None = None,
) -> SnapshotRow:
    return SnapshotRow(
        id=snap_id,
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        provider="aws",
        status=status,
        coverage={"us-east-1/vm": "ok"},
        completed_at=completed_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# FastAPI test app with mocked auth and DB
# ---------------------------------------------------------------------------


def _build_test_app() -> FastAPI:
    """Build a minimal FastAPI app with the inventory router."""
    from api.auth import AuthenticatedUser
    from api.routers import inventory

    app = FastAPI()

    mock_user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="test@example.com",
        workspace_id=WORKSPACE_ID,
        roles=["analyst"],
        token_kind="api_token",
    )

    async def _mock_db() -> AsyncGenerator[AsyncSession, None]:
        async with _session_factory() as session:
            yield session

    async def _mock_current_user() -> AuthenticatedUser:
        return mock_user

    async def _mock_workspace_id() -> uuid.UUID:
        return WORKSPACE_ID

    # Override the dependencies that the router actually uses
    from api.auth import get_current_user
    from api.dependencies import get_db_session, get_workspace_id

    app.dependency_overrides[get_db_session] = _mock_db
    app.dependency_overrides[get_current_user] = _mock_current_user
    app.dependency_overrides[get_workspace_id] = _mock_workspace_id

    app.include_router(inventory.router)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """Create tables, seed data, yield for tests, drop tables."""
    await _create_tables()

    async with _session_factory() as session:
        # Workspace
        ws = WorkspaceRow(
            id=WORKSPACE_ID,
            name="Test Workspace",
            slug="test-workspace",
            settings={},
        )
        session.add(ws)

        # Connection
        conn = ConnectionRow(
            id=CONNECTION_ID,
            workspace_id=WORKSPACE_ID,
            provider="aws",
            name="Test Connection",
            mode="read-only",
            metadata_={},
        )
        session.add(conn)

        # Snapshots — older first
        snap1 = _make_snapshot(SNAPSHOT_ID_1, completed_at=datetime(2024, 1, 1, tzinfo=UTC))
        snap2 = _make_snapshot(SNAPSHOT_ID_2, completed_at=datetime(2024, 2, 1, tzinfo=UTC))
        session.add(snap1)
        session.add(snap2)

        # Resources in snapshot 1
        for i in range(3):
            session.add(_make_resource(
                native_id=f"i-snap1-{i:04d}",
                snapshot_id=SNAPSHOT_ID_1,
                vcpu=2 * (i + 1),
                region="us-east-1",
            ))

        # Resources in snapshot 2 (latest)
        for i in range(5):
            session.add(_make_resource(
                native_id=f"i-snap2-{i:04d}",
                snapshot_id=SNAPSHOT_ID_2,
                vcpu=2 * (i + 1),
                region="us-east-1" if i < 3 else "eu-west-1",
            ))

        # A high-vCPU instance
        session.add(_make_resource(
            native_id="i-highvcpu",
            snapshot_id=SNAPSHOT_ID_2,
            vcpu=64,
            memory_gib=256.0,
        ))

        await session.commit()

    yield

    await _drop_tables()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInventoryVMs:
    def setup_method(self) -> None:
        self.app = _build_test_app()
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def test_list_vms_returns_items(self) -> None:
        """GET /vms returns a VMListResponse with items and total."""
        resp = self.client.get("/inventory/vms?snapshot_id=" + str(SNAPSHOT_ID_2))
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) > 0

    def test_list_vms_filter_min_vcpu(self) -> None:
        """min_vcpu filter excludes instances with fewer vCPUs."""
        resp = self.client.get(f"/inventory/vms?snapshot_id={SNAPSHOT_ID_2}&min_vcpu=32")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            vcpu = item["spec"].get("vcpu", 0)
            assert vcpu >= 32

    def test_list_vms_snapshot_time_in_response(self) -> None:
        """snapshot_time is present in the response."""
        resp = self.client.get(f"/inventory/vms?snapshot_id={SNAPSHOT_ID_2}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id"] == str(SNAPSHOT_ID_2)
        assert data["snapshot_time"] is not None

    def test_list_vms_defaults_to_latest_snapshot(self) -> None:
        """Without snapshot_id, defaults to the latest completed snapshot."""
        resp = self.client.get("/inventory/vms")
        assert resp.status_code == 200
        data = resp.json()
        # Latest snapshot is SNAPSHOT_ID_2
        assert data["snapshot_id"] == str(SNAPSHOT_ID_2)

    def test_list_vms_filter_by_region(self) -> None:
        """Region filter returns only matching resources."""
        resp = self.client.get(f"/inventory/vms?snapshot_id={SNAPSHOT_ID_2}&region=eu-west-1")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["region"] == "eu-west-1"

    def test_list_vms_pagination(self) -> None:
        """limit and offset work correctly."""
        resp1 = self.client.get(f"/inventory/vms?snapshot_id={SNAPSHOT_ID_2}&limit=2&offset=0")
        resp2 = self.client.get(f"/inventory/vms?snapshot_id={SNAPSHOT_ID_2}&limit=2&offset=2")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        ids1 = {item["id"] for item in resp1.json()["items"]}
        ids2 = {item["id"] for item in resp2.json()["items"]}
        assert ids1.isdisjoint(ids2)


class TestInventoryResourceDetail:
    def setup_method(self) -> None:
        self.app = _build_test_app()
        self.client = TestClient(self.app, raise_server_exceptions=True)
        # Get a valid resource ID from the DB
        resp = self.client.get(f"/inventory/vms?snapshot_id={SNAPSHOT_ID_2}&limit=1")
        self.resource_id: str | None = None
        if resp.status_code == 200:
            items = resp.json()["items"]
            self.resource_id = items[0]["id"] if items else None

    def test_get_resource_returns_full_spec(self) -> None:
        """GET /resources/{id} returns spec and provenance."""
        if not self.resource_id:
            pytest.skip("No resources seeded")
        resp = self.client.get(f"/inventory/resources/{self.resource_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "resource" in data
        assert "provenance" in data
        assert "edges_out" in data
        assert "edges_in" in data
        assert data["resource"]["id"] == self.resource_id

    def test_get_resource_404_for_unknown_id(self) -> None:
        """Returns 404 for a non-existent resource ID."""
        fake_id = str(uuid.uuid4())
        resp = self.client.get(f"/inventory/resources/{fake_id}")
        assert resp.status_code == 404

    def test_get_resource_in_workspace(self) -> None:
        """Resources accessible within the workspace."""
        if not self.resource_id:
            pytest.skip("No resources seeded")
        resp = self.client.get(f"/inventory/resources/{self.resource_id}")
        assert resp.status_code == 200
        assert resp.json()["resource"]["workspace_id"] == str(WORKSPACE_ID)


class TestInventoryDiff:
    def setup_method(self) -> None:
        self.app = _build_test_app()
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def test_diff_identifies_added_removed(self) -> None:
        """Diff between snapshot 1 and snapshot 2 shows added resources."""
        resp = self.client.get(
            f"/inventory/diff?snapshot_id_a={SNAPSHOT_ID_1}&snapshot_id_b={SNAPSHOT_ID_2}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id_a"] == str(SNAPSHOT_ID_1)
        assert data["snapshot_id_b"] == str(SNAPSHOT_ID_2)

        # snap2 has i-snap2-NNNN which didn't exist in snap1
        added = data["added"]
        removed = data["removed"]
        assert any(nid.startswith("i-snap2-") for nid in added)
        assert any(nid.startswith("i-snap1-") for nid in removed)

    def test_diff_empty_when_same_snapshot(self) -> None:
        """Diff of a snapshot against itself returns no changes."""
        resp = self.client.get(
            f"/inventory/diff?snapshot_id_a={SNAPSHOT_ID_2}&snapshot_id_b={SNAPSHOT_ID_2}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == []
        assert data["removed"] == []
        assert data["changed"] == []
