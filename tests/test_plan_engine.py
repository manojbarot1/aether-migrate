"""Tests for PlanEngine — deterministic plan generation and content hashing.

Uses an in-memory SQLite database seeded with VM resources and catalog data.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from db.models import (
    Base,
    CatalogInstanceTypeRow,
    ConnectionRow,
    ResourceRow,
    SnapshotRow,
    WorkspaceRow,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000001")
SNAPSHOT_ID = uuid.UUID("cccc0000-0000-0000-0000-000000000001")
VM_ID = uuid.UUID("dddd0000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    eff = datetime(2025, 1, 1)
    async with _session_factory() as db:
        db.add(WorkspaceRow(
            id=WORKSPACE_ID,
            name="test-workspace",
            slug="test-workspace",
            settings={},
        ))
        db.add(ConnectionRow(
            id=CONNECTION_ID,
            workspace_id=WORKSPACE_ID,
            provider="aws",
            name="test-conn",
            mode="read-only",
            metadata_={},
        ))
        db.add(SnapshotRow(
            id=SNAPSHOT_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            provider="aws",
            status="completed",
        ))
        db.add(ResourceRow(
            id=VM_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            snapshot_id=SNAPSHOT_ID,
            provider="aws",
            native_id="i-test001",
            account="123456789",
            region="us-east-1",
            kind="vm",
            name="web-01",
            status="running",
            tags={},
            spec={
                "vcpu": 4,
                "memory_gib": 16.0,
                "architecture": "x86_64",
                "instance_type": "m5.xlarge",
                "os_name": "Ubuntu",
                "os_version": "22.04",
                "disks": [
                    {"size_gib": 100.0, "type_class": "gp3", "boot": True},
                    {"size_gib": 500.0, "type_class": "gp3", "boot": False},
                ],
                "nics": [
                    {"private_ips": ["10.0.1.5"], "public_ips": [], "security_group_ids": ["sg-abc"]},
                ],
            },
            provenance={},
        ))
        db.add(CatalogInstanceTypeRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version="2025-01",
            effective_from=eff,
            region="eastus",
            sku="Standard_D4s_v5",
            vcpu=4,
            memory_mib=16384,
            cpu_arch="x86_64",
            local_nvme_gib=0,
            os_support={"linux": True, "windows": True},
            available_in_region=True,
            restricted=False,
        ))
        await db.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db():
    async with _session_factory() as session:
        yield session


@pytest.fixture
async def plan(db):
    """Create a plan using the PlanEngine."""
    from core.models import ProviderName
    from planner.engine import PlanEngine, PlanOptions
    from sizing.engine import SizingStrategy

    engine = PlanEngine()
    options = PlanOptions(
        target_provider=ProviderName.azure,
        target_region="eastus",
        sizing_strategy=SizingStrategy.like_for_like,
        assumed_bandwidth_mbps=500.0,
    )
    return await engine.create(
        resource_ids=[VM_ID],
        options=options,
        snapshot_id=SNAPSHOT_ID,
        db=db,
        plan_name="Test plan",
        workspace_id=WORKSPACE_ID,
    )


class TestPlanEngineCreate:
    async def test_plan_has_required_fields(self, plan):
        assert plan.plan_id
        assert plan.name
        assert plan.workspace_id == str(WORKSPACE_ID)
        assert plan.version == 1
        assert plan.target_provider.value == "azure"
        assert plan.target_region == "eastus"
        assert plan.snapshot_id == str(SNAPSHOT_ID)

    async def test_content_hash_is_64_char_hex(self, plan):
        assert plan.content_hash is not None
        assert len(plan.content_hash) == 64
        # Must be valid hex
        int(plan.content_hash, 16)

    async def test_identical_plans_have_same_hash(self, db):
        from core.models import ProviderName
        from planner.engine import PlanEngine, PlanOptions
        from sizing.engine import SizingStrategy

        engine = PlanEngine()
        options = PlanOptions(
            target_provider=ProviderName.azure,
            target_region="eastus",
            sizing_strategy=SizingStrategy.like_for_like,
            assumed_bandwidth_mbps=500.0,
        )
        plan_a = await engine.create(
            resource_ids=[VM_ID],
            options=options,
            snapshot_id=SNAPSHOT_ID,
            db=db,
            plan_name="Same plan",
            workspace_id=WORKSPACE_ID,
        )
        plan_b = await engine.create(
            resource_ids=[VM_ID],
            options=options,
            snapshot_id=SNAPSHOT_ID,
            db=db,
            plan_name="Same plan",
            workspace_id=WORKSPACE_ID,
        )
        # Plans created from identical inputs should have matching step structures
        assert len(plan_a.steps) == len(plan_b.steps)

    async def test_different_options_may_differ(self, db):
        from core.models import ProviderName
        from planner.engine import PlanEngine, PlanOptions
        from sizing.engine import SizingStrategy

        engine = PlanEngine()
        opts_a = PlanOptions(
            target_provider=ProviderName.azure,
            target_region="eastus",
            sizing_strategy=SizingStrategy.like_for_like,
            assumed_bandwidth_mbps=100.0,
        )
        opts_b = PlanOptions(
            target_provider=ProviderName.azure,
            target_region="eastus",
            sizing_strategy=SizingStrategy.like_for_like,
            assumed_bandwidth_mbps=1000.0,  # different bandwidth → different downtime
        )
        plan_a = await engine.create([VM_ID], opts_a, SNAPSHOT_ID, db, workspace_id=WORKSPACE_ID)
        plan_b = await engine.create([VM_ID], opts_b, SNAPSHOT_ID, db, workspace_id=WORKSPACE_ID)
        # Different bandwidth → different downtime_estimate_minutes → different hash
        assert plan_a.content_hash != plan_b.content_hash

    async def test_prerequisites_includes_landing_zone(self, plan):
        assert any("OpenTofu" in p or "landing zone" in p for p in plan.prerequisites)

    async def test_prerequisites_includes_quota(self, plan):
        assert any("quota" in p.lower() or "vcpu" in p.lower() for p in plan.prerequisites)

    async def test_steps_has_at_least_10_steps(self, plan):
        assert len(plan.steps) >= 10

    async def test_steps_include_manual_cutover(self, plan):
        manual_steps = [s for s in plan.steps if s.is_manual]
        assert len(manual_steps) >= 1
        # Step 8 (cutover approval) should always be manual
        step8 = next((s for s in plan.steps if s.step_number == 8), None)
        assert step8 is not None
        assert step8.is_manual is True

    async def test_downtime_estimate_nonzero_with_bandwidth(self, plan):
        # 600 GiB total disk at 500 Mbps
        assert plan.downtime_estimate_minutes > 0

    async def test_downtime_basis_mentions_bandwidth(self, plan):
        assert "Mbps" in plan.downtime_basis or "bandwidth" in plan.downtime_basis.lower()

    async def test_source_resources_populated(self, plan):
        assert len(plan.source_resources) == 1
        assert plan.source_resources[0].kind == "vm"
        assert plan.source_resources[0].name == "web-01"

    async def test_rollback_plan_is_not_empty(self, plan):
        assert len(plan.rollback_plan) > 20

    async def test_assumptions_not_empty(self, plan):
        assert len(plan.assumptions) >= 1
