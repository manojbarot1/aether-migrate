"""Tests for catalog versioning helpers.

Uses an in-memory SQLite database; no PostgreSQL required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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


def _make_it_row(
    provider: str = "azure",
    region: str = "eastus",
    sku: str = "Standard_D4s_v5",
    catalog_version: str = "2025-01",
    effective_from: datetime | None = None,
) -> CatalogInstanceTypeRow:
    if effective_from is None:
        effective_from = datetime.now(UTC).replace(tzinfo=None)
    return CatalogInstanceTypeRow(
        id=uuid.uuid4(),
        provider=provider,
        catalog_version=catalog_version,
        effective_from=effective_from,
        region=region,
        sku=sku,
        vcpu=4,
        memory_mib=16384,
        cpu_arch="x86_64",
        gpu_count=0,
        local_nvme_gib=0,
        os_support={"linux": True, "windows": True},
        available_in_region=True,
        restricted=False,
    )


class TestCurrentCatalogVersion:
    def test_returns_yyyy_mm_format(self) -> None:
        """current_catalog_version returns YYYY-MM format."""
        from catalog.versioning import current_catalog_version

        version = current_catalog_version()
        assert len(version) == 7
        assert version[4] == "-"
        year, month = version.split("-")
        assert year.isdigit() and int(year) >= 2025
        assert month.isdigit() and 1 <= int(month) <= 12


class TestGetLatestCatalogVersion:
    async def test_returns_none_when_no_data(self) -> None:
        """Returns None when no catalog data exists for provider."""
        from catalog.versioning import get_latest_catalog_version

        async with _session_factory() as db:
            result = await get_latest_catalog_version(db, "gcp")
        assert result is None

    async def test_returns_version_and_datetime(self) -> None:
        """Returns (version, effective_from) after seeding."""
        from catalog.versioning import get_latest_catalog_version

        row = _make_it_row(provider="azure", catalog_version="2025-01")
        async with _session_factory() as db:
            db.add(row)
            await db.commit()

        async with _session_factory() as db:
            result = await get_latest_catalog_version(db, "azure")

        assert result is not None
        version, eff = result
        assert version == "2025-01"
        assert isinstance(eff, datetime)


class TestIsCatalogStale:
    async def test_stale_when_no_data(self) -> None:
        """is_catalog_stale returns True when no records exist."""
        from catalog.versioning import is_catalog_stale

        async with _session_factory() as db:
            stale = await is_catalog_stale(db, "ibm")
        assert stale is True

    async def test_not_stale_when_recent(self) -> None:
        """is_catalog_stale returns False for recently synced data."""
        from catalog.versioning import is_catalog_stale

        recent_eff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        row = _make_it_row(
            provider="aws",
            sku="m5.xlarge",
            effective_from=recent_eff,
        )
        async with _session_factory() as db:
            db.add(row)
            await db.commit()

        async with _session_factory() as db:
            stale = await is_catalog_stale(db, "aws", max_age_hours=48)
        assert stale is False

    async def test_stale_when_old(self) -> None:
        """is_catalog_stale returns True when data is older than max_age_hours."""
        from catalog.versioning import is_catalog_stale

        old_eff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=72)
        row = _make_it_row(
            provider="gcp",
            sku="n2-standard-4",
            effective_from=old_eff,
        )
        async with _session_factory() as db:
            db.add(row)
            await db.commit()

        async with _session_factory() as db:
            stale = await is_catalog_stale(db, "gcp", max_age_hours=48)
        assert stale is True
