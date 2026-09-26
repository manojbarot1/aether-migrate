"""Discovery end to end: API → connector activities → (moto) AWS → Postgres → inventory API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import httpx
import pytest
from fastapi import FastAPI
from moto import mock_aws

from aether.workers.connector import ConnectorActivities
from aether.workflows.discovery import DiscoveryInput, FinalizeInput, RegionInput

from .conftest import FakeBao, make_member

pytestmark = pytest.mark.integration
REGION = "eu-west-1"


class _InlineDiscovery:
    """Runs the DiscoverConnection workflow's activities in-process, in workflow order."""

    def __init__(self, acts: ConnectorActivities) -> None:
        self.acts = acts

    async def start_workflow(self, _wf: Any, inp: DiscoveryInput, **_: Any) -> None:
        regions = await self.acts.discovery_list_regions(inp)
        results = [await self.acts.discovery_region(RegionInput(discovery=inp, region=r)) for r in regions]
        await self.acts.discovery_finalize(FinalizeInput(discovery=inp, regions=regions, results=results))


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        iam = boto3.client("iam", region_name="us-east-1")
        iam.create_user(UserName="aether")
        key = iam.create_access_key(UserName="aether")["AccessKey"]
        ec2 = boto3.client("ec2", region_name=REGION)
        images = ec2.describe_images(Owners=["amazon"])["Images"]
        ami = next(i["ImageId"] for i in images if "ubuntu" in (i.get("Name") or "").lower())
        big = ec2.run_instances(
            ImageId=ami,
            InstanceType="r5.2xlarge",
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": "db-prod-01"}, {"Key": "tier", "Value": "db"}],
                }
            ],
        )
        small = ec2.run_instances(
            ImageId=ami,
            InstanceType="t3.micro",
            MinCount=3,
            MaxCount=3,
            TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "web"}]}],
        )
        yield {
            "key": key,
            "big": big["Instances"][0]["InstanceId"],
            "small": [i["InstanceId"] for i in small["Instances"]],
        }


async def test_discovery_end_to_end(
    app: FastAPI, client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao, aws: dict[str, Any]
) -> None:
    app.state.temporal = _InlineDiscovery(ConnectorActivities(bao))
    ws = workspace["id"]
    admin = await make_member(client, ws, "connection-admin")
    viewer = await make_member(client, ws, "viewer")
    conn = (
        await client.post(
            f"/api/v1/workspaces/{ws}/connections",
            json={
                "name": "aws-moto",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key", "regions": [REGION], "home_region": REGION},
                "secret": {
                    "access_key_id": aws["key"]["AccessKeyId"],
                    "secret_access_key": aws["key"]["SecretAccessKey"],
                },
            },
            headers=admin,
        )
    ).json()

    # Viewers cannot start discovery; analysts and above can.
    r = await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=viewer)
    assert r.status_code == 403
    r = await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=admin)
    assert r.status_code == 202, r.text
    snap_id = r.json()["id"]

    snap = (await client.get(f"/api/v1/workspaces/{ws}/snapshots/{snap_id}", headers=viewer)).json()
    assert snap["status"] == "complete", snap
    assert snap["regions"] == [REGION]
    assert snap["stats"]["resources"]["vm"] == 4
    assert all(c["status"] == "ok" for c in snap["coverage"] if not c["kind"].startswith("pricing:"))
    # Moto has no Price List API: the pricing gap is reported, but does not make the snapshot partial.
    assert any(c["kind"] == "pricing:ec2" and c["status"] != "ok" for c in snap["coverage"])

    # Filtering: >= 8 vCPU and >= 32 GiB finds only the r5.2xlarge.
    page = (
        await client.get(
            f"/api/v1/workspaces/{ws}/inventory/resources",
            params={"type": "vm", "min_vcpu": 8, "min_memory_gib": 32},
            headers=viewer,
        )
    ).json()
    assert page["total"] == 1
    vm = page["items"][0]
    assert (vm["native_id"], vm["name"], vm["vcpu"], vm["memory_mib"]) == (aws["big"], "db-prod-01", 8, 65536)

    by_tag = (
        await client.get(
            f"/api/v1/workspaces/{ws}/inventory/resources", params={"tag": "tier=db"}, headers=viewer
        )
    ).json()
    assert [i["native_id"] for i in by_tag["items"]] == [aws["big"]]
    by_name = (
        await client.get(
            f"/api/v1/workspaces/{ws}/inventory/resources",
            params={"q": "web", "sort": "name"},
            headers=viewer,
        )
    ).json()
    assert by_name["total"] == 3

    detail = (
        await client.get(f"/api/v1/workspaces/{ws}/inventory/resources/{vm['id']}", headers=viewer)
    ).json()
    assert detail["spec"]["source_sku"] == "r5.2xlarge"
    assert detail["snapshot"]["id"] == snap_id
    kinds = {(n["direction"], n["kind"], n["resource"]["type"]) for n in detail["neighbours"]}
    assert ("out", "in_subnet", "subnet") in kinds
    assert ("in", "attached_to", "disk") in kinds

    summary = (await client.get(f"/api/v1/workspaces/{ws}/inventory/summary", headers=viewer)).json()
    assert summary["resources_by_type"]["vm"] == 4
    assert summary["total_vcpu"] == 8 + 3 * 2
    assert sum(b["count"] for b in summary["vms_by_status"]) == 4
    assert {b["key"] for b in summary["vms_by_arch"]} == {"x86_64"}

    # A second run creates a new snapshot; inventory queries use the latest one only.
    r2 = await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=admin)
    assert r2.status_code == 202
    again = (await client.get(f"/api/v1/workspaces/{ws}/inventory/resources", headers=viewer)).json()
    assert again["total"] == 4
    assert {i["snapshot_id"] for i in again["items"]} == {r2.json()["id"]}

    # Audited, including aggregated cloud API calls.
    audit = (
        await client.get(f"/api/v1/workspaces/{ws}/audit", params={"action": "discovery."}, headers=viewer)
    ).json()["items"]
    runs = [e for e in audit if e["action"] == "discovery.run"]
    assert runs
    assert runs[0]["details"]["cloud_calls"].get("ec2:DescribeInstances", 0) >= 1


async def test_other_workspace_cannot_see_inventory(
    app: FastAPI, client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao, aws: dict[str, Any]
) -> None:
    from .conftest import ADMIN

    app.state.temporal = _InlineDiscovery(ConnectorActivities(bao))
    ws = workspace["id"]
    conn = (
        await client.post(
            f"/api/v1/workspaces/{ws}/connections",
            json={
                "name": "a",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key", "regions": [REGION], "home_region": REGION},
                "secret": {
                    "access_key_id": aws["key"]["AccessKeyId"],
                    "secret_access_key": aws["key"]["SecretAccessKey"],
                },
            },
            headers=ADMIN,
        )
    ).json()
    await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=ADMIN)
    items = (await client.get(f"/api/v1/workspaces/{ws}/inventory/resources", headers=ADMIN)).json()["items"]
    assert items

    other = (
        await client.post("/api/v1/workspaces", json={"slug": "other-ws-x", "name": "o"}, headers=ADMIN)
    ).json()
    member = await make_member(client, other["id"], "admin")
    # Resource ids from workspace A are not visible through workspace B (RLS + explicit check).
    r = await client.get(
        f"/api/v1/workspaces/{other['id']}/inventory/resources/{items[0]['id']}", headers=member
    )
    assert r.status_code == 404
    assert (await client.get(f"/api/v1/workspaces/{other['id']}/inventory/resources", headers=member)).json()[
        "total"
    ] == 0


async def test_topology_of_discovered_network(
    app: FastAPI, client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao, aws: dict[str, Any]
) -> None:
    from .conftest import ADMIN

    app.state.temporal = _InlineDiscovery(ConnectorActivities(bao))
    ws = workspace["id"]
    conn = (
        await client.post(
            f"/api/v1/workspaces/{ws}/connections",
            json={
                "name": "topo",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key", "regions": [REGION], "home_region": REGION},
                "secret": {
                    "access_key_id": aws["key"]["AccessKeyId"],
                    "secret_access_key": aws["key"]["SecretAccessKey"],
                },
            },
            headers=ADMIN,
        )
    ).json()
    await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=ADMIN)
    nets = (
        await client.get(
            f"/api/v1/workspaces/{ws}/inventory/resources", params={"type": "network"}, headers=ADMIN
        )
    ).json()["items"]
    assert nets
    topo = (
        await client.get(f"/api/v1/workspaces/{ws}/inventory/topology/{nets[0]['id']}", headers=ADMIN)
    ).json()
    types = {n["type"] for n in topo["nodes"]}
    assert {"network", "subnet", "vm"} <= types
    vms = [n for n in topo["nodes"] if n["type"] == "vm"]
    subnet_ids = {n["id"] for n in topo["nodes"] if n["type"] == "subnet"}
    assert len(vms) == 4
    assert all(v["parent"] in subnet_ids for v in vms)
    assert topo["mermaid"].startswith("flowchart LR")
    assert topo["truncated"] is False
    # Unknown / non-network ids are 404s.
    r = await client.get(f"/api/v1/workspaces/{ws}/inventory/topology/{vms[0]['id']}", headers=ADMIN)
    assert r.status_code == 404


async def test_compare_sizes_and_prices_discovered_vms(
    app: FastAPI, client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao, aws: dict[str, Any]
) -> None:
    from datetime import date

    from aether.catalog.azure import curated_specs
    from aether.catalog.store import upsert_disks, upsert_fx, upsert_prices, upsert_specs
    from aether.core.catalog import DiskTier, FxRate, Price, PriceModel
    from aether.db.session import session_scope

    from .conftest import ADMIN

    region = "westeurope"

    def p(sku: str, hourly: float, model: PriceModel = PriceModel.ON_DEMAND) -> Price:
        return Price(
            provider="azure",
            region=region,
            sku=sku,
            os="linux",
            model=model,
            hourly_usd=hourly,
            source="test",
        )

    async with session_scope() as s:
        await upsert_specs(s, curated_specs())
        await upsert_prices(
            s,
            [
                p("Standard_E8s_v5", 0.504),
                p("Standard_E8s_v5", 0.3, PriceModel.RESERVED_1Y),
                p("Standard_F2s_v2", 0.096),
                p("Standard_D2s_v5", 0.115),
            ],
            None,
        )
        await upsert_disks(
            s,
            [
                DiskTier(
                    provider="azure",
                    region=region,
                    disk_class="premium_ssd",
                    tier="P4",
                    size_gib=32,
                    iops=120,
                    monthly_usd=5.81,
                    source="test",
                )
            ],
        )
        await upsert_fx(s, [FxRate(currency="EUR", per_usd=0.877, rate_date=date(2026, 9, 25))])

    app.state.temporal = _InlineDiscovery(ConnectorActivities(bao))
    ws = workspace["id"]
    conn = (
        await client.post(
            f"/api/v1/workspaces/{ws}/connections",
            json={
                "name": "cmp",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key", "regions": [REGION], "home_region": REGION},
                "secret": {
                    "access_key_id": aws["key"]["AccessKeyId"],
                    "secret_access_key": aws["key"]["SecretAccessKey"],
                },
            },
            headers=ADMIN,
        )
    ).json()
    await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=ADMIN)
    vms = (await client.get(f"/api/v1/workspaces/{ws}/inventory/resources", headers=ADMIN)).json()["items"]
    ids = {v["native_id"]: v["id"] for v in vms}

    viewer = await make_member(client, ws, "viewer")
    body = {
        "resource_ids": [ids[aws["big"]], ids[aws["small"][0]]],
        "target_region": region,
        "options": {"currency": "EUR"},
    }
    assert (
        await client.post(f"/api/v1/workspaces/{ws}/compare", json=body, headers=viewer)
    ).status_code == 403
    r = await client.post(f"/api/v1/workspaces/{ws}/compare", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["currency"], out["fx_per_usd"]) == ("EUR", 0.877)
    by_native = {i["native_id"]: i for i in out["items"]}
    big = by_native[aws["big"]]
    assert big["sizing"]["candidates"], big["sizing"]
    assert big["sizing"]["candidates"][0]["sku"] == "Standard_E8s_v5"  # r5.2xlarge = 8 vCPU / 64 GiB
    assert big["cost"]["target"]["compute_monthly_usd"]["on_demand"] == round(0.504 * 730, 2)
    assert big["cost"]["target"]["compute_monthly_usd"]["reserved_1y"] == round(0.3 * 730, 2)
    small = by_native[aws["small"][0]]
    assert (
        small["sizing"]["candidates"][0]["sku"] == "Standard_F2s_v2"
    )  # t3.micro: closest shape with a price
    # Moto has no Price List API, so source prices are honestly unavailable.
    assert out["source_prices_available"] is False
    assert out["totals"]["target_monthly_usd"]["on_demand"] is not None

    # Unpriced region is a clear 422, not a silent zero.
    bad = await client.post(
        f"/api/v1/workspaces/{ws}/compare", json={**body, "target_region": "mars"}, headers=ADMIN
    )
    assert bad.status_code == 422
    assert "catalog sync" in bad.json()["error"]["message"]

    status = (await client.get("/api/v1/catalog/status", headers=viewer)).json()
    assert any(x["region"] == region for x in status["azure"])


async def test_assessment_run_and_acknowledgement(
    app: FastAPI, client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao, aws: dict[str, Any]
) -> None:
    from aether.catalog.azure import curated_specs
    from aether.catalog.store import upsert_prices, upsert_specs
    from aether.core.catalog import Price, PriceModel
    from aether.db.session import session_scope

    from .conftest import ADMIN

    async with session_scope() as s:
        await upsert_specs(s, curated_specs())
        await upsert_prices(
            s,
            [
                Price(
                    provider="azure",
                    region="northeurope",
                    sku=sku,
                    os="linux",
                    model=PriceModel.ON_DEMAND,
                    hourly_usd=h,
                    source="test",
                )
                for sku, h in (("Standard_E8s_v5", 0.5), ("Standard_F2s_v2", 0.09))
            ],
            None,
        )

    app.state.temporal = _InlineDiscovery(ConnectorActivities(bao))
    ws = workspace["id"]
    conn = (
        await client.post(
            f"/api/v1/workspaces/{ws}/connections",
            json={
                "name": "asmt",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key", "regions": [REGION], "home_region": REGION},
                "secret": {
                    "access_key_id": aws["key"]["AccessKeyId"],
                    "secret_access_key": aws["key"]["SecretAccessKey"],
                },
            },
            headers=ADMIN,
        )
    ).json()
    await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=ADMIN)
    vms = (await client.get(f"/api/v1/workspaces/{ws}/inventory/resources", headers=ADMIN)).json()["items"]
    body = {"resource_ids": [v["id"] for v in vms], "target_region": "northeurope"}

    r = await client.post(f"/api/v1/workspaces/{ws}/assessments", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["summary"]["vms"] == 4
    assert sum(run["summary"]["readiness"].values()) == 4
    assert run["summary"]["quota_needs"]  # aggregated vCPU per family
    big = next(i for i in run["items"] if i["native_id"] == aws["big"])
    assert big["target_sku"] == "Standard_E8s_v5"
    rules = {f["rule_id"] for f in big["findings"]}
    assert {"DRV-001", "NET-001"} <= rules

    # Acknowledge a warning; it is applied on the next run and audited.
    ack = {"native_id": aws["big"], "rule_id": "DRV-001", "reason": "Hyper-V modules verified in initramfs"}
    assert (
        await client.put(f"/api/v1/workspaces/{ws}/acknowledgements", json=ack, headers=ADMIN)
    ).status_code == 204
    again = (await client.post(f"/api/v1/workspaces/{ws}/assessments", json=body, headers=ADMIN)).json()
    big2 = next(i for i in again["items"] if i["native_id"] == aws["big"])
    drv = next(f for f in big2["findings"] if f["rule_id"] == "DRV-001")
    assert drv["acknowledged"] is True
    assert drv["acknowledgement"]["reason"].startswith("Hyper-V")
    assert big2["score"] > big["score"]

    listing = (await client.get(f"/api/v1/workspaces/{ws}/assessments", headers=ADMIN)).json()
    assert listing[0]["id"] == again["id"]
    assert listing[0]["items"] is None  # list is summary-only
    bad = await client.put(
        f"/api/v1/workspaces/{ws}/acknowledgements", json={**ack, "rule_id": "XX-999"}, headers=ADMIN
    )
    assert bad.status_code == 422
    audit = (
        await client.get(f"/api/v1/workspaces/{ws}/audit", params={"action": "assessment."}, headers=ADMIN)
    ).json()["items"]
    assert {e["action"] for e in audit} >= {"assessment.run", "assessment.acknowledge"}


async def test_plan_lifecycle_four_eyes_immutability_and_exports(
    app: FastAPI,
    client: httpx.AsyncClient,
    workspace: dict[str, Any],
    bao: FakeBao,
    aws: dict[str, Any],
    owner_engine: Any,
) -> None:
    import io
    import zipfile

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from aether.catalog.azure import curated_specs
    from aether.catalog.store import upsert_prices, upsert_specs
    from aether.core.catalog import Price, PriceModel
    from aether.db.session import session_scope

    from .conftest import ADMIN

    async with session_scope() as s:
        await upsert_specs(s, curated_specs())
        await upsert_prices(
            s,
            [
                Price(
                    provider="azure",
                    region="uksouth",
                    sku=sku,
                    os="linux",
                    model=PriceModel.ON_DEMAND,
                    hourly_usd=h,
                    source="test",
                )
                for sku, h in (("Standard_E8s_v5", 0.5), ("Standard_F2s_v2", 0.09))
            ],
            None,
        )
    app.state.temporal = _InlineDiscovery(ConnectorActivities(bao))
    ws = workspace["id"]
    author = await make_member(client, ws, "analyst")
    approver = await make_member(client, ws, "approver")
    conn = (
        await client.post(
            f"/api/v1/workspaces/{ws}/connections",
            json={
                "name": "plan",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key", "regions": [REGION], "home_region": REGION},
                "secret": {
                    "access_key_id": aws["key"]["AccessKeyId"],
                    "secret_access_key": aws["key"]["SecretAccessKey"],
                },
            },
            headers=ADMIN,
        )
    ).json()
    await client.post(f"/api/v1/workspaces/{ws}/connections/{conn['id']}/discover", headers=ADMIN)
    vms = (await client.get(f"/api/v1/workspaces/{ws}/inventory/resources", headers=author)).json()["items"]
    run = (
        await client.post(
            f"/api/v1/workspaces/{ws}/assessments",
            json={"resource_ids": [v["id"] for v in vms], "target_region": "uksouth"},
            headers=author,
        )
    ).json()

    r = await client.post(
        f"/api/v1/workspaces/{ws}/plans",
        json={"name": "Pilot wave", "assessment_run_id": run["id"]},
        headers=author,
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert (plan["status"], plan["version"]) == ("draft", 1)
    assert len(plan["content_hash"]) == 64
    assert plan["totals"]["vms"] == 4
    assert {"main.tf", "outputs.tf", "versions.tf", "variables.tf"} <= set(plan["iac_files"])
    steps = plan["content"]["waves"][0]["steps"]
    assert all(s["pre_check"] and s["post_check"] and s["compensation"] for s in steps)

    base = f"/api/v1/workspaces/{ws}/plans/{plan['id']}"
    # Analysts lack the approver role; drafts cannot be reviewed.
    denied = await client.post(
        f"{base}/review",
        json={"decision": "approve", "comment": "ok", "content_hash": plan["content_hash"]},
        headers=author,
    )
    assert denied.status_code == 403
    assert (await client.post(f"{base}/submit", headers=author)).status_code == 200
    assert (await client.post(f"{base}/submit", headers=author)).status_code == 409  # no longer a draft

    # Four-eyes: an author who is also an approver still cannot review their own plan.
    own_plan = (
        await client.post(
            f"/api/v1/workspaces/{ws}/plans",
            json={"name": "Admin plan", "assessment_run_id": run["id"]},
            headers=ADMIN,
        )
    ).json()
    await client.post(f"/api/v1/workspaces/{ws}/plans/{own_plan['id']}/submit", headers=ADMIN)
    own = await client.post(
        f"/api/v1/workspaces/{ws}/plans/{own_plan['id']}/review",
        json={"decision": "approve", "comment": "self", "content_hash": own_plan["content_hash"]},
        headers=ADMIN,
    )
    assert own.status_code == 403
    assert "own plan" in own.json()["error"]["message"]

    stale = await client.post(
        f"{base}/review",
        json={"decision": "approve", "comment": "looks fine", "content_hash": "0" * 64},
        headers=approver,
    )
    assert stale.status_code == 409
    approved = await client.post(
        f"{base}/review",
        json={"decision": "approve", "comment": "Reviewed waves", "content_hash": plan["content_hash"]},
        headers=approver,
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["reviews"][0]["content_hash"] == plan["content_hash"]
    assert body["reviews"][0]["expires_at"]

    # Content is immutable even for the schema owner.
    with pytest.raises(DBAPIError, match="immutable"):
        async with owner_engine.begin() as c:
            await c.execute(text("UPDATE plans SET content = '{}'::jsonb WHERE id = :i"), {"i": plan["id"]})

    # Revising creates v2 and supersedes v1; the diff reports changes.
    rev = await client.post(
        f"{base}/revise", json={"options": {"replication_bandwidth_mbps": 100}}, headers=author
    )
    assert rev.status_code == 201, rev.text
    v2 = rev.json()
    assert (v2["version"], v2["status"]) == (2, "draft")
    assert v2["content_hash"] != plan["content_hash"]
    assert [v["status"] for v in v2["versions"]] == ["superseded", "draft"]
    d = (
        await client.get(f"/api/v1/workspaces/{ws}/plans/{v2['id']}/diff/{plan['id']}", headers=author)
    ).json()
    assert d["added"] == []
    assert d["removed"] == []

    md = await client.get(f"{base}/export/markdown", headers=author)
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.text.startswith("# Migration plan: Pilot wave")
    js = (await client.get(f"{base}/export/json", headers=author)).json()
    assert js["content_hash"] == plan["content_hash"]
    z = await client.get(f"{base}/export/opentofu", headers=author)
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert any(n.endswith("/main.tf") for n in names)
    assert any(n.endswith("/PLAN_HASH") for n in names)
    tf = (await client.get(f"{base}/iac/main.tf", headers=author)).text
    assert 'resource "azurerm_virtual_network"' in tf
