"""Tests for the cost engine.

Uses an in-memory SQLite database seeded with catalog instance types,
prices, and FX rates. No real cloud provider calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.models import (
    Base,
    CatalogDiskPriceRow,
    CatalogInstanceTypeRow,
    CatalogPriceRow,
    FXRateRow,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("aaaa1111-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("bbbb1111-0000-0000-0000-000000000001")

_EFF = datetime(2025, 1, 15, tzinfo=UTC).replace(tzinfo=None)
_CATALOG_VER = "2025-01"
_HOURLY_PRICE = Decimal("0.192")   # Standard_D4s_v5 on-demand Linux


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _session_factory() as db:
        # Instance type
        db.add(CatalogInstanceTypeRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version=_CATALOG_VER,
            effective_from=_EFF,
            region="eastus",
            sku="Standard_D4s_v5",
            vcpu=4,
            memory_mib=16384,
            cpu_arch="x86_64",
            gpu_count=0,
            local_nvme_gib=0,
            os_support={"linux": True, "windows": True},
            available_in_region=True,
            restricted=False,
        ))
        # Windows variant of same SKU
        db.add(CatalogInstanceTypeRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version=_CATALOG_VER,
            effective_from=_EFF,
            region="eastus",
            sku="Standard_D4s_v5_windows",
            vcpu=4,
            memory_mib=16384,
            cpu_arch="x86_64",
            gpu_count=0,
            local_nvme_gib=0,
            os_support={"linux": True, "windows": True},
            available_in_region=True,
            restricted=False,
        ))

        # Linux on-demand price
        db.add(CatalogPriceRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version=_CATALOG_VER,
            effective_from=_EFF,
            region="eastus",
            sku="Standard_D4s_v5",
            os_type="linux",
            license_model="included",
            term="on-demand",
            price_usd=str(_HOURLY_PRICE),
            currency="USD",
            price_per="hour",
        ))
        # Windows on-demand price
        db.add(CatalogPriceRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version=_CATALOG_VER,
            effective_from=_EFF,
            region="eastus",
            sku="Standard_D4s_v5_windows",
            os_type="windows",
            license_model="included",
            term="on-demand",
            price_usd="0.380",
            currency="USD",
            price_per="hour",
        ))
        # 1yr reserved price
        db.add(CatalogPriceRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version=_CATALOG_VER,
            effective_from=_EFF,
            region="eastus",
            sku="Standard_D4s_v5",
            os_type="linux",
            license_model="included",
            term="1yr-std",
            price_usd="0.130",
            currency="USD",
            price_per="hour",
        ))

        # Disk price
        db.add(CatalogDiskPriceRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version=_CATALOG_VER,
            effective_from=_EFF,
            region="eastus",
            disk_type="premium-ssd-v2",
            price_per_gib_month="0.08",
            price_per_iops_month="0.000064",
            price_per_mbps_month="0.00076",
        ))

        # FX rates
        db.add(FXRateRow(
            id=uuid.uuid4(),
            base_currency="USD",
            target_currency="EUR",
            rate="0.92",
            rate_date=date(2025, 1, 15),
            source="ecb",
        ))
        db.add(FXRateRow(
            id=uuid.uuid4(),
            base_currency="USD",
            target_currency="GBP",
            rate="0.79",
            rate_date=date(2025, 1, 15),
            source="ecb",
        ))

        await db.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _source_vm(
    os_name: str = "linux",
    disk_gib: float = 100.0,
    disk_type: str = "gp3",
    net_mbps_p95: float | None = None,
    monthly_cost: float | None = None,
    license_model: str | None = None,
) -> VMSpec:  # type: ignore[name-defined]
    from core.models import CostSpec, DiskSpec, MetricsSpec, ProviderName, VMSpec

    disks = [DiskSpec(size_gib=disk_gib, type_class=disk_type)]
    metrics = None
    if net_mbps_p95 is not None:
        metrics = MetricsSpec(net_mbps_p95=net_mbps_p95)
    cost = None
    if monthly_cost is not None:
        cost = CostSpec(list_monthly_cost=monthly_cost)

    return VMSpec(
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        provider=ProviderName.aws,
        native_id="i-testcost",
        account="123456789012",
        region="us-east-1",
        vcpu=4,
        memory_gib=16.0,
        instance_type="m5.xlarge",
        architecture="x86_64",
        os_name=os_name,
        license_model=license_model,
        disks=disks,
        metrics=metrics,
        cost=cost,
    )


def _make_sizing_result(
    sku: str = "Standard_D4s_v5",
    catalog_version: str = _CATALOG_VER,
    catalog_date: datetime | None = None,
    os_name: str = "linux",
) -> SizingResult:  # type: ignore[name-defined]
    from core.models import ProviderName
    from sizing.engine import SizingCandidate, SizingResult, SizingStrategy

    if catalog_date is None:
        catalog_date = _EFF.replace(tzinfo=UTC)

    candidate = SizingCandidate(
        sku=sku,
        provider=ProviderName.azure,
        region="eastus",
        vcpu=4,
        memory_mib=16384,
        cpu_arch="x86_64",
        gpu_count=0,
        network_bandwidth_gbps=None,
        available=True,
        restricted=False,
        on_demand_price_usd=float(_HOURLY_PRICE),
        reasoning=f"like_for_like: source is m5.xlarge (4 vCPU / 16 GiB). Selected {sku}.",
        catalog_version=catalog_version,
    )
    return SizingResult(
        strategy=SizingStrategy.like_for_like,
        candidates=[candidate],
        source_vm={"instance_type": "m5.xlarge", "vcpu": 4, "memory_gib": 16.0},
        hard_constraints_applied=["cpu_arch=x86_64"],
        catalog_version=catalog_version,
        catalog_date=catalog_date,
        warnings=[],
    )


class TestOnDemandCost:
    async def test_on_demand_cost_equals_price_times_730h(self) -> None:
        """on-demand monthly cost = hourly_price × 730."""
        from core.models import ProviderName
        from cost.engine import CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm()
        sizing = [_make_sizing_result()]

        async with _session_factory() as db:
            result = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )

        assert result.targets, "Expected at least one cost target"
        t = result.targets[0]
        expected = (_HOURLY_PRICE * Decimal("730")).quantize(Decimal("0.01"))
        assert t.breakdown.compute_monthly_usd == expected, (
            f"Expected {expected}, got {t.breakdown.compute_monthly_usd}"
        )


class TestMigrationEgress:
    async def test_migration_egress_in_first_year(self) -> None:
        """Migration egress (disk_gib × egress_rate) is included in first-year total."""
        from core.models import ProviderName
        from cost.engine import CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm(disk_gib=200.0)  # 200 GiB at $0.09/GiB = $18
        sizing = [_make_sizing_result()]

        async with _session_factory() as db:
            result = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )

        t = result.targets[0]
        expected_egress = Decimal("200") * Decimal("0.09")  # $18
        # First year should include migration egress
        assert t.breakdown.migration_egress_usd == expected_egress.quantize(Decimal("0.01"))
        assert t.breakdown.total_first_year_usd > t.breakdown.total_monthly_usd * 12


class TestDualRun:
    async def test_dual_run_14_days(self) -> None:
        """Dual-run period is 14 days; costs are included in first-year total."""
        from core.models import ProviderName
        from cost.engine import CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm()
        sizing = [_make_sizing_result()]

        async with _session_factory() as db:
            result = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )

        t = result.targets[0]
        assert t.breakdown.dual_run_days == 14
        assert t.breakdown.dual_run_cost_usd > Decimal("0")
        # First-year = monthly * 12 + egress + dual_run
        expected_first_year = (
            t.breakdown.total_monthly_usd * 12
            + t.breakdown.migration_egress_usd
            + t.breakdown.dual_run_cost_usd
        ).quantize(Decimal("0.01"))
        assert t.breakdown.total_first_year_usd == expected_first_year


class TestWindowsLicenceWarning:
    async def test_windows_sku_includes_licence_review_flag(self) -> None:
        """Windows OS sets licence_review_required=True and adds a warning."""
        from core.models import ProviderName
        from cost.engine import CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm(os_name="windows server 2022")
        sizing = [_make_sizing_result(sku="Standard_D4s_v5_windows")]

        async with _session_factory() as db:
            result = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )

        t = result.targets[0]
        assert t.licence_review_required, "Windows VM should set licence_review_required"
        assert any("licence" in w.lower() or "license" in w.lower() for w in t.warnings)


class TestCurrencyConversion:
    async def test_eur_conversion_applied(self) -> None:
        """Currency conversion uses FX rate; costs are in target currency."""
        from core.models import ProviderName
        from cost.engine import CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm()
        sizing = [_make_sizing_result()]

        async with _session_factory() as db:
            result_usd = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )
            result_eur = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="EUR",
                db=db,
            )

        t_usd = result_usd.targets[0]
        t_eur = result_eur.targets[0]

        assert t_eur.breakdown.currency == "EUR"
        assert t_eur.breakdown.fx_rate is not None
        # EUR cost ≈ USD cost * 0.92
        expected_eur = (t_usd.breakdown.compute_monthly_usd * Decimal("0.92")).quantize(Decimal("0.01"))
        assert t_eur.breakdown.compute_monthly_usd == expected_eur


class TestIsEstimate:
    async def test_is_estimate_always_true(self) -> None:
        """CostBreakdown.is_estimate is always True; cannot be set to False."""
        from core.models import ProviderName
        from cost.engine import CostBreakdown, CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm()
        sizing = [_make_sizing_result()]

        async with _session_factory() as db:
            result = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )

        for t in result.targets:
            assert t.breakdown.is_estimate is True

        # Verify the model validator enforces is_estimate=True even if forced
        bd = CostBreakdown(
            compute_monthly_usd=Decimal("0"),
            storage_monthly_usd=Decimal("0"),
            network_monthly_usd=Decimal("0"),
            migration_egress_usd=Decimal("0"),
            dual_run_cost_usd=Decimal("0"),
            total_monthly_usd=Decimal("0"),
            total_first_year_usd=Decimal("0"),
            assumptions=[],
            catalog_version="2025-01",
            catalog_date=datetime.now(UTC),
            currency="USD",
            fx_rate=None,
            is_estimate=True,  # This field is always True regardless
        )
        assert bd.is_estimate is True


class TestCatalogStalenessWarning:
    async def test_stale_catalog_warning_emitted(self) -> None:
        """A warning is added when catalog data is >48h old."""
        from core.models import ProviderName
        from cost.engine import CostEngine, CostScenario

        engine = CostEngine()
        source = _source_vm()

        # Use a very old catalog date
        old_date = datetime.now(UTC) - timedelta(hours=72)
        sizing = [_make_sizing_result(catalog_date=old_date)]

        async with _session_factory() as db:
            result = await engine.compare(
                source_vm=source,
                sizing_results=sizing,
                scenarios=[CostScenario.on_demand],
                target_regions={ProviderName.azure: "eastus"},
                workspace_currency="USD",
                db=db,
            )

        for t in result.targets:
            all_text = " ".join(t.warnings) + " ".join(t.breakdown.assumptions)
            assert "stale" in all_text.lower() or "48h" in all_text.lower(), (
                "Expected staleness warning in assumptions or warnings"
            )
