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
        ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
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
    assert all(c["status"] == "ok" for c in snap["coverage"])

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
