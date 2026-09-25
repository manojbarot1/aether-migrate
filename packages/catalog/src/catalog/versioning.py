"""AETHER MIGRATE — catalog versioning helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


def current_catalog_version() -> str:
    """Return YYYY-MM format string for the current month.

    This is the version tag assigned to catalog records synced during the
    current calendar month.
    """
    now = datetime.now(UTC)
    return now.strftime("%Y-%m")


async def get_latest_catalog_version(
    db: AsyncSession,
    provider: str,
) -> tuple[str, datetime] | None:
    """Return (version, effective_from) of the most recent complete catalog.

    Returns ``None`` if no catalog data exists for *provider*.
    """
    from db.models import CatalogInstanceTypeRow

    result = await db.execute(
        select(
            CatalogInstanceTypeRow.catalog_version,
            func.max(CatalogInstanceTypeRow.effective_from).label("max_eff"),
        )
        .where(CatalogInstanceTypeRow.provider == provider)
        .group_by(CatalogInstanceTypeRow.catalog_version)
        .order_by(func.max(CatalogInstanceTypeRow.effective_from).desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return row.catalog_version, row.max_eff


async def is_catalog_stale(
    db: AsyncSession,
    provider: str,
    max_age_hours: int = 48,
) -> bool:
    """Return True if no catalog data exists or last sync > max_age_hours ago."""
    result = await get_latest_catalog_version(db, provider)
    if result is None:
        return True

    _, effective_from = result
    now = datetime.now(UTC)
    # effective_from is stored timezone-naive (UTC)
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=UTC)

    age_hours = (now - effective_from).total_seconds() / 3600
    if age_hours > max_age_hours:
        log.warning(
            "catalog.stale",
            provider=provider,
            age_hours=round(age_hours, 1),
            max_age_hours=max_age_hours,
        )
        return True
    return False
