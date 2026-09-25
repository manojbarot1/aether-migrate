"""Tests for the Azure catalog sync (httpx mock, no real network).

Tests:
  - Retail Prices API response is parsed and stored correctly
  - Pagination (nextPageLink) is followed
  - Restricted SKUs from Resource SKUs API are flagged
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from db.models import Base, CatalogInstanceTypeRow
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
async def create_tables():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _retail_price_item(
    sku: str = "Standard_D4s_v5",
    region: str = "eastus",
    price: float = 0.192,
    price_type: str = "Consumption",
    product_name: str = "Virtual Machines Dsv5 Series",
    reservation_term: str | None = None,
) -> dict[str, Any]:
    return {
        "armSkuName": sku,
        "skuName": sku,
        "armRegionName": region,
        "retailPrice": price,
        "unitPrice": price,
        "currencyCode": "USD",
        "type": price_type,
        "priceType": price_type,
        "productName": product_name,
        "reservationTerm": reservation_term,
        "serviceName": "Virtual Machines",
    }


class TestAzureRetailPricesParsing:
    """Test AzureCatalogSync._parse_price_item."""

    def setup_method(self) -> None:
        from catalog.sync.azure import AzureCatalogSync
        self.syncer = AzureCatalogSync()

    def test_parses_consumption_as_on_demand(self) -> None:
        """Consumption price type maps to on-demand term."""
        item = _retail_price_item(price_type="Consumption")
        eff = datetime.now(UTC).replace(tzinfo=None)
        result = self.syncer._parse_price_item(item, "eastus", "2025-01", eff)
        assert result is not None
        assert result.term == "on-demand"
        assert result.sku == "Standard_D4s_v5"
        assert result.price_usd == 0.192
        assert result.os_type == "linux"

    def test_parses_reservation_1yr_as_1yr_std(self) -> None:
        """1 Year reservation maps to 1yr-std term."""
        item = _retail_price_item(
            price_type="Reservation",
            reservation_term="1 Year",
        )
        eff = datetime.now(UTC).replace(tzinfo=None)
        result = self.syncer._parse_price_item(item, "eastus", "2025-01", eff)
        assert result is not None
        assert result.term == "1yr-std"

    def test_parses_reservation_3yr_as_3yr_std(self) -> None:
        """3 Year reservation maps to 3yr-std term."""
        item = _retail_price_item(
            price_type="Reservation",
            reservation_term="3 Years",
        )
        eff = datetime.now(UTC).replace(tzinfo=None)
        result = self.syncer._parse_price_item(item, "eastus", "2025-01", eff)
        assert result is not None
        assert result.term == "3yr-std"

    def test_windows_product_name_sets_os_type(self) -> None:
        """Windows in product name → os_type=windows."""
        item = _retail_price_item(product_name="Windows Virtual Machines Dsv5 Series")
        eff = datetime.now(UTC).replace(tzinfo=None)
        result = self.syncer._parse_price_item(item, "eastus", "2025-01", eff)
        assert result is not None
        assert result.os_type == "windows"

    def test_zero_price_returns_none(self) -> None:
        """Zero retail price → item is skipped (returns None)."""
        item = _retail_price_item(price=0.0)
        eff = datetime.now(UTC).replace(tzinfo=None)
        result = self.syncer._parse_price_item(item, "eastus", "2025-01", eff)
        assert result is None


class TestAzurePricePagination:
    """Test that sync_prices follows nextPageLink."""

    async def test_pagination_followed(self) -> None:
        """sync_prices reads both pages when nextPageLink is present."""
        from catalog.sync.azure import AzureCatalogSync

        page1_data = {
            "Items": [_retail_price_item(sku="Standard_D2s_v5", price=0.096)],
            "NextPageLink": "https://prices.azure.com/api/retail/prices?skip=100",
        }
        page2_data = {
            "Items": [_retail_price_item(sku="Standard_D4s_v5", price=0.192)],
            "NextPageLink": None,
        }

        call_count = {"n": 0}

        async def mock_get(url: str, **kwargs: Any) -> MagicMock:
            call_count["n"] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=page1_data if call_count["n"] == 1 else page2_data)
            return resp

        syncer = AzureCatalogSync()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            async with _session_factory() as db:
                result = await syncer.sync_prices(["eastus"], db)

        assert call_count["n"] == 2, "Should have made 2 HTTP calls for pagination"
        assert not result.errors


class TestAzureRestrictedSKUs:
    """Test that Resource SKUs restrictions are reflected in instance type rows."""

    async def test_restricted_sku_flagged(self) -> None:
        """SKUs with restrictions from Resource SKUs API are stored as restricted=True.

        The azure-mgmt-compute package is not installed in the test environment,
        so we patch the SDK classes inside the sync method using sys.modules injection.
        """
        import sys

        from catalog.sync.azure import AzureCatalogSync

        syncer = AzureCatalogSync()

        # Build mock compute client
        mock_size = MagicMock()
        mock_size.name = "Standard_D4s_v5_restricted"
        mock_size.number_of_cores = 4
        mock_size.memory_in_mb = 16384
        mock_size.max_data_disk_count = 8
        mock_size.os_disk_size_in_mb = 1047552
        mock_size.resource_disk_size_in_mb = 32768

        mock_sku = MagicMock()
        mock_sku.resource_type = "virtualMachines"
        mock_sku.name = "Standard_D4s_v5_restricted"
        loc_info = MagicMock()
        loc_info.location = "eastus"
        mock_sku.location_info = [loc_info]
        mock_sku.restrictions = [MagicMock()]  # Has restrictions

        mock_compute = MagicMock()
        mock_compute.virtual_machine_sizes.list.return_value = [mock_size]
        mock_compute.resource_skus.list.return_value = [mock_sku]

        # Inject fake azure SDK modules so the local imports inside sync_instance_types succeed
        fake_identity = MagicMock()
        fake_identity.DefaultAzureCredential = MagicMock

        fake_compute_module = MagicMock()
        fake_compute_module.ComputeManagementClient = MagicMock(return_value=mock_compute)

        with patch.dict(sys.modules, {
            "azure": MagicMock(),
            "azure.identity": fake_identity,
            "azure.mgmt": MagicMock(),
            "azure.mgmt.compute": fake_compute_module,
        }):
            async with _session_factory() as db:
                result = await syncer.sync_instance_types(
                    ["eastus"],
                    db,
                    credential=MagicMock(),
                    subscription_id="test-sub",
                )

        assert not result.errors, f"Unexpected errors: {result.errors}"
        # Verify row was stored as restricted
        async with _session_factory() as db:
            from sqlalchemy import select
            rows = (await db.execute(
                select(CatalogInstanceTypeRow).where(
                    CatalogInstanceTypeRow.sku == "Standard_D4s_v5_restricted",
                    CatalogInstanceTypeRow.region == "eastus",
                )
            )).scalars().all()
        assert any(r.restricted for r in rows), "Restricted SKU should be stored with restricted=True"
