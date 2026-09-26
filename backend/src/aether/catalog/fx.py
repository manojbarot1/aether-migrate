"""FX reference rates from the European Central Bank (daily, EUR-based, public)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import httpx

from aether.core.catalog import FxRate

ECB_DAILY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def parse_ecb(xml_text: str) -> list[FxRate]:
    """Convert ECB's EUR-based rates to units-per-USD for every listed currency (+EUR, USD)."""
    root = ET.fromstring(xml_text)  # noqa: S314 - see module note
    day_cube = next((c for c in root.iter() if c.get("time")), None)
    if day_cube is None:
        raise ValueError("ECB feed has no dated rates")
    rate_date = date.fromisoformat(day_cube.get("time", ""))
    per_eur = {c.get("currency"): float(c.get("rate", "0")) for c in day_cube if c.get("currency")}
    usd_per_eur = per_eur.get("USD")
    if not usd_per_eur:
        raise ValueError("ECB feed has no USD rate")
    rates = [
        FxRate(currency="USD", per_usd=1.0, rate_date=rate_date),
        FxRate(currency="EUR", per_usd=1 / usd_per_eur, rate_date=rate_date),
    ]
    rates += [
        FxRate(currency=cur, per_usd=rate / usd_per_eur, rate_date=rate_date)
        for cur, rate in sorted(per_eur.items())
        if cur and cur != "USD"
    ]
    return rates


async def fetch_ecb(client: httpx.AsyncClient) -> list[FxRate]:
    resp = await client.get(ECB_DAILY, timeout=20)
    resp.raise_for_status()
    return parse_ecb(resp.text)
