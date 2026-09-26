"""Catalog parsers, sizing engine and cost model against hand-computed expectations."""

from __future__ import annotations

import json

import pytest

from aether.catalog.aws import parse_ebs_products, parse_ec2_products
from aether.catalog.azure import curated_specs, parse_disk_prices, parse_vm_prices
from aether.catalog.fx import parse_ecb
from aether.core.catalog import DiskTier, InstanceSpec, PriceModel
from aether.core.inventory import DiskSpec, VmSpec
from aether.cost.engine import CostOptions, estimate, pick_tier, required_iops
from aether.sizing.engine import Strategy, recommend

REGION = "westeurope"


def _az(
    sku: str,
    kind: str,
    product: str,
    price: float,
    *,
    sku_name: str | None = None,
    term: str | None = None,
    meter: str | None = None,
) -> dict[str, object]:
    return {
        "armSkuName": sku,
        "armRegionName": REGION,
        "currencyCode": "USD",
        "type": kind,
        "productName": product,
        "skuName": sku_name or sku.removeprefix("Standard_").replace("_", " "),
        "meterName": meter or "x",
        "reservationTerm": term,
        "unitPrice": price,
        "effectiveStartDate": "2026-06-01T00:00:00Z",
    }


# Recorded shape (Azure Retail Prices API, westeurope, 2026-09): Standard_D4s_v5.
AZURE_ITEMS = [
    _az("Standard_D4s_v5", "Consumption", "Virtual Machines Dsv5 Series", 0.23),
    _az("Standard_D4s_v5", "Consumption", "Virtual Machines Dsv5 Series Windows", 0.414),
    _az("Standard_D4s_v5", "Reservation", "Virtual Machines Dsv5 Series", 1243.0, term="1 Year"),
    _az("Standard_D4s_v5", "Reservation", "Virtual Machines Dsv5 Series", 2388.0, term="3 Years"),
    _az("Standard_D4s_v5", "Consumption", "Virtual Machines Dsv5 Series", 0.0425, sku_name="D4s v5 Spot"),
    _az(
        "Standard_D4s_v5",
        "Consumption",
        "Virtual Machines Dsv5 Series",
        0.046,
        sku_name="D4s v5 Low Priority",
    ),
    _az("Standard_D4s_v5", "DevTestConsumption", "Virtual Machines Dsv5 Series Windows", 0.23),
]


def test_azure_vm_price_parsing() -> None:
    prices = {
        (p.sku, p.os, p.model): p.hourly_usd
        for p in parse_vm_prices(AZURE_ITEMS, REGION, {"Standard_D4s_v5"})
    }
    assert prices[("Standard_D4s_v5", "linux", PriceModel.ON_DEMAND)] == 0.23
    assert prices[("Standard_D4s_v5", "windows", PriceModel.ON_DEMAND)] == 0.414
    # Reservation unitPrice is the term total.
    assert prices[("Standard_D4s_v5", "linux", PriceModel.RESERVED_1Y)] == pytest.approx(1243 / 8760)
    assert prices[("Standard_D4s_v5", "linux", PriceModel.RESERVED_3Y)] == pytest.approx(2388 / 26280)
    # Windows reserved = compute reservation + licence uplift (0.414 - 0.23).
    assert prices[("Standard_D4s_v5", "windows", PriceModel.RESERVED_1Y)] == pytest.approx(
        1243 / 8760 + 0.184
    )
    assert len(prices) == 6  # spot, low-priority and dev/test excluded


def test_azure_disk_tier_parsing() -> None:
    items = [
        {
            "productName": "Premium SSD Managed Disks",
            "armRegionName": REGION,
            "type": "Consumption",
            "meterName": "P10 LRS Disk",
            "unitPrice": 21.68,
        },
        {
            "productName": "Premium SSD Managed Disks",
            "armRegionName": REGION,
            "type": "Consumption",
            "meterName": "P10 LRS Disk Mount",
            "unitPrice": 1.18,
        },
        {
            "productName": "Premium SSD Managed Disks",
            "armRegionName": REGION,
            "type": "Consumption",
            "meterName": "P10 ZRS Disk",
            "unitPrice": 32.5,
        },
        {
            "productName": "Standard SSD Managed Disks",
            "armRegionName": REGION,
            "type": "Consumption",
            "meterName": "E30 LRS Disk",
            "unitPrice": 76.8,
        },
    ]
    tiers = {(t.disk_class, t.tier): t for t in parse_disk_prices(items, REGION)}
    assert set(tiers) == {("premium_ssd", "P10"), ("standard_ssd", "E30")}
    assert (tiers[("premium_ssd", "P10")].size_gib, tiers[("premium_ssd", "P10")].iops) == (128, 500)
    assert tiers[("standard_ssd", "E30")].monthly_usd == 76.8


def test_curated_specs_are_consistent() -> None:
    specs = curated_specs()
    skus = [s.sku for s in specs]
    assert len(skus) == len(set(skus))
    by = {s.sku: s for s in specs}
    assert (by["Standard_D4s_v5"].vcpu, by["Standard_D4s_v5"].memory_mib) == (4, 16384)
    assert by["Standard_E96s_v5"].memory_mib == 672 * 1024
    assert by["Standard_D4ps_v5"].cpu_arch == "arm64"
    assert by["Standard_F8s_v2"].local_disk_gib == 64
    assert all(s.spec_source == "curated" for s in specs)


def _aws_product(sku: str, os_: str, od: str, ri1: str, ri3: str, tenancy: str = "Shared") -> str:
    def dim(price: str, unit: str = "Hrs") -> dict[str, object]:
        return {"d": {"unit": unit, "pricePerUnit": {"USD": price}}}

    def ri(years: str, price: str, option: str = "No Upfront") -> dict[str, object]:
        return {
            "termAttributes": {
                "LeaseContractLength": years,
                "OfferingClass": "standard",
                "PurchaseOption": option,
            },
            "priceDimensions": dim(price),
        }

    return json.dumps(
        {
            "product": {
                "attributes": {
                    "instanceType": sku,
                    "operatingSystem": os_,
                    "tenancy": tenancy,
                    "preInstalledSw": "NA",
                    "capacitystatus": "Used",
                    "licenseModel": "No License required",
                }
            },
            "terms": {
                "OnDemand": {"t": {"priceDimensions": dim(od)}},
                "Reserved": {
                    "a": ri("1yr", ri1),
                    "b": ri("3yr", ri3),
                    "c": ri("1yr", "0.0001", "All Upfront"),
                },
            },
        }
    )


def test_aws_price_parsing() -> None:
    items = [
        _aws_product("m5.xlarge", "Linux", "0.2300000000", "0.1450000000", "0.0990000000"),
        _aws_product("m5.xlarge", "Linux", "9.9", "9.9", "9.9", tenancy="Dedicated"),
    ]
    prices = {(p.os, p.model): p.hourly_usd for p in parse_ec2_products(items, "eu-central-1")}
    assert prices == {
        ("linux", PriceModel.ON_DEMAND): 0.23,
        ("linux", PriceModel.RESERVED_1Y): 0.145,
        ("linux", PriceModel.RESERVED_3Y): 0.099,
    }
    ebs = parse_ebs_products(
        [
            json.dumps(
                {
                    "product": {"attributes": {"volumeApiName": "gp3"}},
                    "terms": {
                        "OnDemand": {
                            "t": {
                                "priceDimensions": {"d": {"unit": "GB-Mo", "pricePerUnit": {"USD": "0.0952"}}}
                            }
                        }
                    },
                }
            )
        ],
        "eu-central-1",
    )
    assert (ebs[0].disk_class, ebs[0].gib_month_usd) == ("gp3", 0.0952)


def test_ecb_fx() -> None:
    xml = """<?xml version="1.0"?><gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
      xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time='2026-09-25'>
      <Cube currency='USD' rate='1.1403'/><Cube currency='PLN' rate='4.2500'/>
      </Cube></Cube></gesmes:Envelope>"""
    rates = {r.currency: r for r in parse_ecb(xml)}
    assert rates["USD"].per_usd == 1.0
    assert rates["EUR"].per_usd == pytest.approx(1 / 1.1403)
    assert rates["PLN"].per_usd == pytest.approx(4.25 / 1.1403)
    assert str(rates["EUR"].rate_date) == "2026-09-25"


# --------------------------------------------------------------------------- sizing

SPECS = [
    InstanceSpec(
        provider="azure",
        sku="D4s",
        family="Dsv5",
        vcpu=4,
        memory_mib=16384,
        cpu_arch="x86_64",
        generation=5,
        spec_source="curated",
    ),
    InstanceSpec(
        provider="azure",
        sku="D4as",
        family="Dasv5",
        vcpu=4,
        memory_mib=16384,
        cpu_arch="x86_64",
        generation=5,
        spec_source="curated",
    ),
    InstanceSpec(
        provider="azure",
        sku="D8s",
        family="Dsv5",
        vcpu=8,
        memory_mib=32768,
        cpu_arch="x86_64",
        generation=5,
        spec_source="curated",
    ),
    InstanceSpec(
        provider="azure",
        sku="E4s",
        family="Esv5",
        vcpu=4,
        memory_mib=32768,
        cpu_arch="x86_64",
        generation=5,
        spec_source="curated",
    ),
    InstanceSpec(
        provider="azure",
        sku="D2s",
        family="Dsv5",
        vcpu=2,
        memory_mib=8192,
        cpu_arch="x86_64",
        generation=5,
        spec_source="curated",
    ),
    InstanceSpec(
        provider="azure",
        sku="D4ds",
        family="Ddsv5",
        vcpu=4,
        memory_mib=16384,
        cpu_arch="x86_64",
        local_disk_gib=150,
        generation=5,
        spec_source="curated",
    ),
    InstanceSpec(
        provider="azure",
        sku="D4ps",
        family="Dpsv5",
        vcpu=4,
        memory_mib=16384,
        cpu_arch="arm64",
        generation=5,
        spec_source="curated",
    ),
]
HOURLY = {"D4s": 0.23, "D4as": 0.207, "D8s": 0.46, "E4s": 0.304, "D2s": 0.115, "D4ds": 0.271, "D4ps": 0.185}


def price(sku: str, os_: str, model: PriceModel = PriceModel.ON_DEMAND) -> float | None:
    base = HOURLY.get(sku)
    if base is None:
        return None
    return {
        PriceModel.ON_DEMAND: base,
        PriceModel.RESERVED_1Y: base * 0.62,
        PriceModel.RESERVED_3Y: base * 0.4,
    }[model] + (0.184 if os_ == "windows" else 0)


def vm(**kw: object) -> VmSpec:
    base = {
        "source_sku": "m5.xlarge",
        "vcpu": 4,
        "memory_mib": 16384,
        "cpu_arch": "x86_64",
        "os_family": "linux",
    }
    return VmSpec.model_validate({**base, **kw})


def test_like_for_like_prefers_exact_shape_then_price() -> None:
    r = recommend(vm(), SPECS, price, Strategy.LIKE_FOR_LIKE)
    assert [c.sku for c in r.candidates] == ["D4as", "D4s", "D4ds"]  # exact 4/16, cheapest first
    assert r.requirement.basis == "allocation"


def test_cheapest_fit_and_constraints() -> None:
    assert recommend(vm(), SPECS, price, Strategy.CHEAPEST_FIT).candidates[0].sku == "D4as"
    arm = recommend(vm(cpu_arch="arm64", source_sku="m7g.xlarge"), SPECS, price)
    assert [c.sku for c in arm.candidates] == ["D4ps"]
    big = recommend(vm(vcpu=4, memory_mib=32768), SPECS, price)
    assert big.candidates[0].sku == "E4s"  # memory-optimised beats a bigger general-purpose size


def test_ephemeral_disk_prefers_local_temp_disk() -> None:
    r = recommend(vm(disks=[{"device": "instance-store", "size_gib": 100, "ephemeral": True}]), SPECS, price)
    assert r.candidates[0].sku == "D4ds"


def test_right_sizing_uses_metrics_or_says_why_not() -> None:
    r = recommend(
        vm(vcpu=8, memory_mib=32768),
        SPECS,
        price,
        Strategy.RIGHT_SIZED,
        utilization={"cpu_p95": 0.2, "mem_p95": 0.4},
    )
    assert r.requirement.basis == "utilization"
    assert (r.requirement.vcpu, r.requirement.memory_mib) == (3, 17040)  # ceil(8*.2*1.3), ceil(32768*.4*1.3)
    assert r.candidates[0].sku == "E4s"  # cheapest with ≥3 vCPU and ≥16.6 GiB
    no_metrics = recommend(vm(), SPECS, price, Strategy.RIGHT_SIZED)
    assert no_metrics.requirement.basis == "allocation"
    assert any("no CPU utilisation metrics" in n for n in no_metrics.requirement.notes)


def test_no_candidate_explains_why() -> None:
    r = recommend(vm(cpu_arch="arm64"), [s for s in SPECS if s.cpu_arch == "x86_64"], price)
    assert r.candidates == []
    assert "no arm64 sizes" in r.warnings[-1]


# --------------------------------------------------------------------------- cost

TIERS = [
    DiskTier(
        provider="azure",
        region=REGION,
        disk_class="premium_ssd",
        tier=t,
        size_gib=s,
        iops=i,
        monthly_usd=m,
        source="t",
    )
    for t, s, i, m in (
        ("P4", 32, 120, 5.81),
        ("P10", 128, 500, 21.68),
        ("P15", 256, 1100, 41.81),
        ("P20", 512, 2300, 80.54),
        ("P30", 1024, 5000, 148.68),
    )
]
EBS = [
    DiskTier(provider="aws", region="eu-central-1", disk_class=c, gib_month_usd=r, source="t")
    for c, r in (("gp2", 0.119), ("gp3", 0.0952), ("io2", 0.149))
]
SRC = {
    ("m5.xlarge", "linux", PriceModel.ON_DEMAND): 0.23,
    ("m5.xlarge", "linux", PriceModel.RESERVED_1Y): 0.145,
    ("m5.xlarge", "linux", PriceModel.RESERVED_3Y): 0.099,
}


def src_price(sku: str, os_: str, model: PriceModel) -> float | None:
    return SRC.get((sku, os_, model))


def test_disk_tier_mapping_uses_provisioned_iops_only() -> None:
    gp3 = DiskSpec(size_gib=200, type_class="gp3", iops=3000)
    assert required_iops(gp3) is None
    assert pick_tier(gp3, TIERS, "premium_ssd")[0].tier == "P15"  # type: ignore[union-attr]
    io2 = DiskSpec(size_gib=200, type_class="io2", iops=4000)
    tier, note = pick_tier(io2, TIERS, "premium_ssd")
    assert (tier.tier, note) == ("P30", None)  # type: ignore[union-attr]
    huge = DiskSpec(size_gib=200, type_class="io2", iops=9000)
    tier, note = pick_tier(huge, TIERS, "premium_ssd")
    assert note is not None
    assert "9000 IOPS" in note


def test_estimate_matches_hand_calculation() -> None:
    v = vm(
        disks=[
            {"device": "/dev/sda1", "size_gib": 8, "type_class": "gp2", "boot": True},
            {"device": "/dev/sdf", "size_gib": 200, "type_class": "gp3", "iops": 3000},
            {"device": "instance-store", "size_gib": 100, "ephemeral": True},
        ]
    )
    c = estimate(v, "D4s", price, TIERS, src_price, EBS, CostOptions())
    # target: 0.23*730 = 167.90 compute; disks P4 5.81 + P15 41.81 = 47.62
    assert c.target.compute_monthly_usd["on_demand"] == 167.9
    assert c.target.disks_monthly_usd == 47.62
    assert c.target.total_monthly_usd["on_demand"] == 215.52
    assert c.target.total_monthly_usd["reserved_3y"] == round(0.23 * 0.4 * 730 + 47.62, 2)
    # source: 167.90 compute; 8*0.119 + 200*0.0952 = 0.95 + 19.04 = 19.99
    assert c.source.disks_monthly_usd == 19.99
    assert c.source.total_monthly_usd["on_demand"] == 187.89
    assert c.source.total_monthly_usd["reserved_1y"] == round(0.145 * 730 + 19.99, 2)
    # one-time: (8 + 200) GiB * 0.09 = 18.72; dual running 215.52 * 14/30 = 100.58
    assert (c.one_time.egress_gib, c.one_time.egress_usd, c.one_time.dual_running_usd) == (208, 18.72, 100.58)
    assert any("ephemeral" in a for a in c.assumptions)


def test_windows_and_missing_prices() -> None:
    w = estimate(vm(os_family="windows"), "D4s", price, TIERS, src_price, EBS, CostOptions())
    assert w.target.compute_monthly_usd["on_demand"] == round((0.23 + 0.184) * 730, 2)
    assert w.source.total_monthly_usd["on_demand"] is None  # no Windows source price in the fixture
    assert any("Windows licence included" in a for a in w.assumptions)
    none = estimate(vm(), None, price, TIERS, src_price, EBS, CostOptions())
    assert none.target.total_monthly_usd["on_demand"] is None
    assert none.one_time.dual_running_usd is None
