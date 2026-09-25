"""Tests for the sizing engine.

Uses an in-memory SQLite database seeded with catalog instance type and
price records. No real cloud provider calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from db.models import Base, CatalogInstanceTypeRow, CatalogPriceRow
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000099")
CONNECTION_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000099")


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    eff = datetime(2025, 1, 15, tzinfo=UTC).replace(tzinfo=None)

    async with _session_factory() as db:
        # Azure instance types in eastus
        skus = [
            # (sku, vcpu, mem_mib, arch, gpu_count, restricted)
            ("Standard_D2s_v5",  2,  4096,  "x86_64", 0, False),
            ("Standard_D4s_v5",  4, 16384,  "x86_64", 0, False),
            ("Standard_D8s_v5",  8, 32768,  "x86_64", 0, False),
            ("Standard_D16s_v5", 16, 65536, "x86_64", 0, False),
            ("Standard_D4s_v3",  4, 16384,  "x86_64", 0, False),  # older gen
            ("Standard_E4s_v5",  4, 32768,  "x86_64", 0, False),  # more memory
            # arm64 SKU
            ("Standard_D4ps_v5", 4, 16384,  "arm64",  0, False),
            # restricted
            ("Standard_D4s_v4",  4, 16384,  "x86_64", 0, True),
            # GPU
            ("Standard_NC4as_T4_v3", 4, 28672, "x86_64", 1, False),
        ]
        for sku, vcpu, mem_mib, arch, gpu, restricted in skus:
            db.add(CatalogInstanceTypeRow(
                id=uuid.uuid4(),
                provider="azure",
                catalog_version="2025-01",
                effective_from=eff,
                region="eastus",
                sku=sku,
                vcpu=vcpu,
                memory_mib=mem_mib,
                cpu_arch=arch,
                gpu_count=gpu,
                gpu_model="Tesla T4" if gpu > 0 else None,
                local_nvme_gib=0,
                os_support={"linux": True, "windows": True},
                available_in_region=not restricted,
                restricted=restricted,
            ))

        # Prices for on-demand Linux
        prices = [
            ("Standard_D2s_v5",  0.096),
            ("Standard_D4s_v5",  0.192),
            ("Standard_D8s_v5",  0.384),
            ("Standard_D16s_v5", 0.768),
            ("Standard_D4s_v3",  0.240),  # older gen = more expensive
            ("Standard_E4s_v5",  0.252),
            ("Standard_D4ps_v5", 0.156),  # arm64
            ("Standard_NC4as_T4_v3", 1.20),
        ]
        for sku, price in prices:
            db.add(CatalogPriceRow(
                id=uuid.uuid4(),
                provider="azure",
                catalog_version="2025-01",
                effective_from=eff,
                region="eastus",
                sku=sku,
                os_type="linux",
                license_model="included",
                term="on-demand",
                price_usd=str(price),
                currency="USD",
                price_per="hour",
            ))

        await db.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _make_source_vm(
    vcpu: int = 4,
    memory_gib: float = 16.0,
    arch: str = "x86_64",
    gpu_count: int = 0,
    gpu_model: str | None = None,
    os_name: str = "linux",
    instance_type: str = "m5.xlarge",
    cpu_p95: float | None = None,
    mem_p95: float | None = None,
) -> VMSpec:  # type: ignore[name-defined]
    from core.models import MetricsSpec, ProviderName, VMSpec

    metrics = None
    if cpu_p95 is not None or mem_p95 is not None:
        metrics = MetricsSpec(cpu_p95=cpu_p95, mem_p95=mem_p95)

    return VMSpec(
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        provider=ProviderName.aws,
        native_id="i-test001",
        account="123456789012",
        region="us-east-1",
        vcpu=vcpu,
        memory_gib=memory_gib,
        gpu_count=gpu_count,
        gpu_model=gpu_model,
        instance_type=instance_type,
        architecture=arch,
        os_name=os_name,
        metrics=metrics,
    )


class TestLikeForLike:
    async def test_exact_vcpu_and_memory_match(self) -> None:
        """like_for_like returns candidates with matching vCPU and memory."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0)

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.like_for_like],
                db=db,
            )

        assert len(results) == 1
        r = results[0]
        # All returned candidates should have 4 vCPU and 16 GiB (16384 MiB)
        for c in r.candidates:
            assert c.vcpu == 4
            assert c.memory_mib == 16384

    async def test_like_for_like_reasoning_mentions_source(self) -> None:
        """Reasoning string references source instance type."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0, instance_type="m5.xlarge")

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.like_for_like],
                db=db,
            )

        r = results[0]
        assert r.candidates, "Expected at least one candidate"
        for c in r.candidates:
            assert "m5.xlarge" in c.reasoning


class TestRightSized:
    async def test_right_sized_uses_p95_with_headroom(self) -> None:
        """right_sized computes minimum vCPU from p95 + 30% headroom."""
        import math

        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        # Source: 8 vCPU, cpu_p95=50% → need ceil(4 * 1.30) = 6 vCPU
        source = _make_source_vm(vcpu=8, memory_gib=32.0, cpu_p95=50.0, mem_p95=80.0)

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.right_sized],
                db=db,
                headroom_pct=0.30,
            )

        r = results[0]
        min_vcpu_expected = math.ceil((50.0 / 100.0) * 8 * 1.30)  # = 6
        # All candidates must satisfy the minimum vCPU
        for c in r.candidates:
            assert c.vcpu >= min_vcpu_expected

    async def test_right_sized_fallback_without_metrics(self) -> None:
        """right_sized falls back to allocation and adds a warning when no metrics."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0)  # no metrics

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.right_sized],
                db=db,
            )

        r = results[0]
        assert any("unavailable" in w.lower() for w in r.warnings), (
            "Expected a warning about metrics being unavailable"
        )

    async def test_right_sized_minimum_requirement_met(self) -> None:
        """right_sized candidates meet at least the source allocation requirements."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0)

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.right_sized],
                db=db,
            )

        r = results[0]
        for c in r.candidates:
            assert c.vcpu >= 4
            assert c.memory_mib >= 16384


class TestArm64Filter:
    async def test_arm64_excludes_x86_candidates(self) -> None:
        """arm64 source VM only returns arm64 candidates."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0, arch="arm64")

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.like_for_like],
                db=db,
            )

        r = results[0]
        for c in r.candidates:
            assert c.cpu_arch == "arm64", f"Expected arm64 but got {c.cpu_arch} for {c.sku}"

    async def test_arm64_constraint_is_reported(self) -> None:
        """Hard constraints list includes the arch constraint."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0, arch="arm64")

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.like_for_like],
                db=db,
            )

        r = results[0]
        assert any("arm64" in c for c in r.hard_constraints_applied)


class TestRestrictedExclusion:
    async def test_restricted_skus_excluded(self) -> None:
        """Restricted SKUs are never returned by the sizing engine."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0)

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[SizingStrategy.like_for_like, SizingStrategy.cheapest_fit],
                db=db,
            )

        all_skus = [c.sku for r in results for c in r.candidates]
        assert "Standard_D4s_v4" not in all_skus, "Restricted SKU should not appear in results"


class TestReasoningString:
    async def test_reasoning_non_empty_and_references_source(self) -> None:
        """Every candidate has a non-empty reasoning string mentioning the source SKU."""
        from core.models import ProviderName
        from sizing.engine import SizingEngine, SizingStrategy

        engine = SizingEngine()
        source = _make_source_vm(vcpu=4, memory_gib=16.0, instance_type="m5.xlarge")

        async with _session_factory() as db:
            results = await engine.recommend(
                source_vm=source,
                target_provider=ProviderName.azure,
                target_region="eastus",
                strategies=[
                    SizingStrategy.like_for_like,
                    SizingStrategy.right_sized,
                    SizingStrategy.cheapest_fit,
                ],
                db=db,
            )

        for r in results:
            for c in r.candidates:
                assert c.reasoning, "Reasoning must be non-empty"
                assert "m5.xlarge" in c.reasoning, "Reasoning should reference source SKU"
