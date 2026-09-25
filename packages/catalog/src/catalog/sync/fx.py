"""AETHER MIGRATE — FX rate sync from ECB reference rates.

Fetches daily foreign exchange rates from the European Central Bank (ECB) XML
feed and stores them in ``fx_rates``.  USD base rates are computed by
inverting EUR/USD and combining with each EUR/target rate.

If the ECB feed is unavailable the sync logs a warning and returns without
raising — downstream cost calculations will fall back to USD.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

import structlog

from catalog.versioning import current_catalog_version  # noqa: F401  (imported for parity)

log = structlog.get_logger(__name__)

# ECB daily reference rates XML endpoint
_ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# Currencies we care about (USD base, so we need USD→EUR implicitly via EUR→USD)
# ECB publishes rates as EUR/currency.  We store them as USD/currency.
_TARGET_CURRENCIES = {"EUR", "GBP", "PLN", "JPY"}

# Namespace used in the ECB XML
_NS = {"gesmes": "http://www.gesmes.org/xml/2002-08-01", "ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}


class FXRateSync:
    """Fetch ECB reference rates and store USD-based FX rates."""

    async def sync(
        self,
        db: AsyncSession,  # type: ignore[name-defined]  # noqa: F821
    ) -> dict[str, Any]:
        """Fetch and store FX rates.  Returns a summary dict.

        If the ECB feed is unreachable, a warning is logged and an empty
        summary is returned — this never raises.
        """
        import httpx
        from db.models import FXRateRow
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(_ECB_URL)
                resp.raise_for_status()
                xml_text = resp.text
        except Exception as exc:  # noqa: BLE001
            log.warning("fx_sync.ecb_unavailable", error=str(exc))
            return {"rates_synced": 0, "errors": [str(exc)]}

        try:
            rates_eur = self._parse_ecb_xml(xml_text)
        except Exception as exc:  # noqa: BLE001
            log.warning("fx_sync.parse_error", error=str(exc))
            return {"rates_synced": 0, "errors": [str(exc)]}

        if "USD" not in rates_eur:
            log.warning("fx_sync.usd_not_found_in_ecb")
            return {"rates_synced": 0, "errors": ["USD rate not found in ECB feed"]}

        eur_usd = rates_eur["USD"]         # EUR/USD exchange rate
        rate_date = date.today()
        values: list[dict[str, Any]] = []

        for currency in _TARGET_CURRENCIES:
            if currency not in rates_eur and currency != "USD":
                continue
            if currency == "USD":
                # USD/USD is trivially 1.0
                usd_rate = Decimal("1.000000")
            else:
                # EUR/currency * (1 / EUR/USD) = USD/currency
                eur_currency = rates_eur[currency]
                usd_rate = Decimal(str(eur_currency / eur_usd)).quantize(
                    Decimal("0.000001")
                )

            values.append(
                {
                    "id": uuid.uuid4(),
                    "base_currency": "USD",
                    "target_currency": currency,
                    "rate": usd_rate,
                    "rate_date": rate_date,
                    "source": "ecb",
                }
            )

        try:
            stmt = pg_insert(FXRateRow).values(values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fx_rates",
                set_={"rate": stmt.excluded.rate},
            )
            await db.execute(stmt)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            log.error("fx_sync.db_error", error=str(exc))
            return {"rates_synced": 0, "errors": [str(exc)]}

        log.info("fx_sync.done", rates_synced=len(values), rate_date=str(rate_date))
        return {"rates_synced": len(values), "errors": []}

    @staticmethod
    def _parse_ecb_xml(xml_text: str) -> dict[str, float]:
        """Parse ECB eurofxref XML and return a dict of currency → float rate.

        Rates are EUR-based (e.g. {"USD": 1.08, "GBP": 0.86}).
        """
        root = ElementTree.fromstring(xml_text)  # noqa: S314  — ECB feed, not user input

        rates: dict[str, float] = {}

        # The XML structure has nested Cube elements
        for cube_outer in root.iter("{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"):
            currency = cube_outer.get("currency")
            rate_str = cube_outer.get("rate")
            if currency and rate_str:
                try:
                    rates[currency] = float(rate_str)
                except ValueError:
                    pass

        return rates
