"""AWS source pricing via the Price List Query API (``pricing:GetProducts``).

Queried with the connection's own read-only credentials, only for the instance types
present in the inventory. Parsing is pure and tested against recorded product JSON.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from aether.core.catalog import DiskTier, Price, PriceModel

SOURCE = "aws-price-list"
PRICING_REGION = "us-east-1"  # the Price List API is served from a few fixed regions

_OS = {"Linux": "linux", "Windows": "windows"}


def _usd(dimensions: dict[str, Any], unit: str = "Hrs") -> float | None:
    for dim in dimensions.values():
        if dim.get("unit") == unit:
            value = dim.get("pricePerUnit", {}).get("USD")
            if value is not None:
                return float(value)
    return None


def parse_ec2_products(price_list: Iterable[str | dict[str, Any]], region: str) -> list[Price]:
    """Shared-tenancy, licence-included, no-pre-installed-software prices.

    Reserved: standard offering class, **No Upfront**, whose hourly rate is directly
    comparable with on-demand (partial/all-upfront terms amortise differently).
    """
    out: list[Price] = []
    for entry in price_list:
        item = json.loads(entry) if isinstance(entry, str) else entry
        attrs = item.get("product", {}).get("attributes", {})
        os_ = _OS.get(attrs.get("operatingSystem", ""))
        if (
            os_ is None
            or attrs.get("tenancy") != "Shared"
            or attrs.get("preInstalledSw") != "NA"
            or attrs.get("capacitystatus", "Used") != "Used"
            or attrs.get("licenseModel", "No License required")
            not in ("No License required", "License Included")
        ):
            continue
        sku = attrs.get("instanceType")
        terms = item.get("terms", {})
        for term in (terms.get("OnDemand") or {}).values():
            hourly = _usd(term.get("priceDimensions", {}))
            if hourly:
                out.append(
                    Price(
                        provider="aws",
                        region=region,
                        sku=sku,
                        os=os_,
                        model=PriceModel.ON_DEMAND,
                        hourly_usd=hourly,
                        source=SOURCE,
                    )
                )
        for term in (terms.get("Reserved") or {}).values():
            ta = term.get("termAttributes", {})
            if ta.get("OfferingClass") != "standard" or ta.get("PurchaseOption") != "No Upfront":
                continue
            model = {"1yr": PriceModel.RESERVED_1Y, "3yr": PriceModel.RESERVED_3Y}.get(
                ta.get("LeaseContractLength", "")
            )
            hourly = _usd(term.get("priceDimensions", {}))
            if model and hourly:
                out.append(
                    Price(
                        provider="aws",
                        region=region,
                        sku=sku,
                        os=os_,
                        model=model,
                        hourly_usd=hourly,
                        source=SOURCE,
                    )
                )
    return out


def parse_ebs_products(price_list: Iterable[str | dict[str, Any]], region: str) -> list[DiskTier]:
    out: list[DiskTier] = []
    for entry in price_list:
        item = json.loads(entry) if isinstance(entry, str) else entry
        attrs = item.get("product", {}).get("attributes", {})
        api_name = attrs.get("volumeApiName")
        if not api_name:
            continue
        for term in (item.get("terms", {}).get("OnDemand") or {}).values():
            gib_month = _usd(term.get("priceDimensions", {}), unit="GB-Mo")
            if gib_month is not None:
                out.append(
                    DiskTier(
                        provider="aws",
                        region=region,
                        disk_class=api_name,
                        gib_month_usd=gib_month,
                        source=SOURCE,
                    )
                )
    return out


def fetch_prices(session: Any, region: str, skus: Iterable[str]) -> tuple[list[Price], list[DiskTier]]:
    """Network I/O (runs in the connector worker)."""
    client = session.client("pricing", region_name=PRICING_REGION)

    def products(filters: list[dict[str, str]]) -> list[str]:
        items: list[str] = []
        for page in client.get_paginator("get_products").paginate(ServiceCode="AmazonEC2", Filters=filters):
            items.extend(page.get("PriceList") or [])
        return items

    base = [{"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}]
    prices: list[Price] = []
    for sku in sorted(set(skus)):
        prices += parse_ec2_products(
            products(
                [
                    *base,
                    {"Type": "TERM_MATCH", "Field": "instanceType", "Value": sku},
                    {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                    {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                    {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                ]
            ),
            region,
        )
    disks = parse_ebs_products(
        products([*base, {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"}]), region
    )
    return prices, disks
