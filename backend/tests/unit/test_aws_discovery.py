"""AWS collection + normalisation against a moto-built estate (no network)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from aether.core.inventory import CoverageStatus, EdgeKind, ResourceType, resource_id
from aether.providers.aws.discovery import collect_region, list_enabled_regions
from aether.providers.aws.normalize import RegionRaw, infer_os, normalize_region

REGION = "eu-central-1"


@pytest.fixture
def estate(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=REGION)
        elb = boto3.client("elbv2", region_name=REGION)
        vpc = ec2.create_vpc(CidrBlock="10.10.0.0/16")["Vpc"]["VpcId"]
        ec2.create_tags(Resources=[vpc], Tags=[{"Key": "Name", "Value": "prod-vpc"}])
        sub_a = ec2.create_subnet(VpcId=vpc, CidrBlock="10.10.1.0/24", AvailabilityZone=f"{REGION}a")[
            "Subnet"
        ]
        sub_b = ec2.create_subnet(VpcId=vpc, CidrBlock="10.10.2.0/24", AvailabilityZone=f"{REGION}b")[
            "Subnet"
        ]
        lb_sg = ec2.create_security_group(GroupName="lb", Description="lb", VpcId=vpc)["GroupId"]
        web_sg = ec2.create_security_group(GroupName="web", Description="web", VpcId=vpc)["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=web_sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "UserIdGroupPairs": [{"GroupId": lb_sg}],
                },
                {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "10.0.0.0/8"}]},
            ],
        )
        ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
        instances = ec2.run_instances(
            ImageId=ami,
            InstanceType="m5.xlarge",
            MinCount=2,
            MaxCount=2,
            SubnetId=sub_a["SubnetId"],
            SecurityGroupIds=[web_sg],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": "web"}, {"Key": "env", "Value": "prod"}],
                }
            ],
        )["Instances"]
        ids = [i["InstanceId"] for i in instances]
        data = ec2.create_volume(Size=200, AvailabilityZone=f"{REGION}a", VolumeType="gp3")["VolumeId"]
        ec2.attach_volume(VolumeId=data, InstanceId=ids[0], Device="/dev/sdf")
        lb = elb.create_load_balancer(
            Name="web-alb", Subnets=[sub_a["SubnetId"], sub_b["SubnetId"]], SecurityGroups=[lb_sg]
        )["LoadBalancers"][0]
        tg = elb.create_target_group(Name="web", Protocol="HTTP", Port=80, VpcId=vpc, TargetType="instance")[
            "TargetGroups"
        ][0]
        elb.register_targets(TargetGroupArn=tg["TargetGroupArn"], Targets=[{"Id": i} for i in ids])
        elb.create_listener(
            LoadBalancerArn=lb["LoadBalancerArn"],
            Protocol="HTTP",
            Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}],
        )
        yield {
            "vpc": vpc,
            "subnet": sub_a["SubnetId"],
            "web_sg": web_sg,
            "lb_sg": lb_sg,
            "ids": ids,
            "data_volume": data,
            "lb_arn": lb["LoadBalancerArn"],
        }


def test_collect_and_normalize(estate: dict[str, Any]) -> None:
    session = boto3.session.Session(region_name=REGION)
    assert REGION in list_enabled_regions(session)
    raw, coverage = collect_region(session, "123456789012", REGION)
    assert {c.status for c in coverage} == {CoverageStatus.OK}
    assert {c.kind for c in coverage} >= {"ec2:instances", "ec2:volumes", "elbv2:load_balancers"}

    snap = uuid.uuid4()
    bundle = normalize_region(snap, raw)
    by_native = {r.native_id: r for r in bundle.resources}

    vm = by_native[estate["ids"][0]]
    assert vm.type == ResourceType.VM
    assert vm.name == "web"
    assert vm.tags == {"Name": "web", "env": "prod"}
    assert vm.status == "running"
    spec = vm.spec
    assert spec["source_sku"] == "m5.xlarge"
    assert spec["vcpu"] == 4
    assert spec["memory_mib"] == 16384
    assert spec["cpu_arch"] == "x86_64"
    sizes = sorted(d.get("size_gib") or 0 for d in spec["disks"])
    assert 200 in sizes  # attached data volume joined from DescribeVolumes
    assert spec["provenance"]["vcpu"] == "discovered"
    assert spec["nics"][0]["security_group_native_ids"] == [estate["web_sg"]]

    sg = by_native[estate["web_sg"]]
    rules = sg.spec["rules"]
    assert {
        "direction": "ingress",
        "protocol": "tcp",
        "port_from": 443,
        "port_to": 443,
        "peer_group_native_id": estate["lb_sg"],
        "action": "allow",
    } in rules
    assert any(r.get("peer_cidr") == "10.0.0.0/8" and r["port_from"] == 22 for r in rules)

    lb = by_native[estate["lb_arn"]]
    assert sorted(lb.spec["target_native_ids"]) == sorted(estate["ids"])

    edges = {(e.from_id, e.to_id, e.kind) for e in bundle.edges}
    rid = lambda n: resource_id(snap, n)  # noqa: E731
    assert (rid(estate["ids"][0]), rid(estate["subnet"]), EdgeKind.IN_SUBNET) in edges
    assert (rid(estate["ids"][0]), rid(estate["web_sg"]), EdgeKind.PROTECTED_BY) in edges
    assert (rid(estate["data_volume"]), rid(estate["ids"][0]), EdgeKind.ATTACHED_TO) in edges
    assert (rid(estate["subnet"]), rid(estate["vpc"]), EdgeKind.IN_NETWORK) in edges
    assert (rid(estate["web_sg"]), rid(estate["lb_sg"]), EdgeKind.REFERENCES) in edges
    for i in estate["ids"]:
        assert (rid(estate["lb_arn"]), rid(i), EdgeKind.ROUTES_TO) in edges
    # Every edge endpoint is a resource in the bundle.
    ids = {r.id for r in bundle.resources}
    assert all(e.from_id in ids and e.to_id in ids for e in bundle.edges)
    # Raw payloads are JSON-safe (boto returns datetimes).
    assert isinstance(vm.raw["LaunchTime"], str)


def test_normalize_is_deterministic(estate: dict[str, Any]) -> None:
    session = boto3.session.Session(region_name=REGION)
    raw, _ = collect_region(session, "123456789012", REGION)
    snap = uuid.uuid4()
    a, b = normalize_region(snap, raw), normalize_region(snap, raw)
    assert a.model_dump() == b.model_dump()


def test_denied_family_degrades_coverage_not_the_region() -> None:
    from botocore.exceptions import ClientError

    class Denying:
        def get_paginator(self, op: str) -> Any:
            raise ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, op)

    class FakeSession:
        def client(self, *_: Any, **__: Any) -> Denying:
            return Denying()

    raw, coverage = collect_region(FakeSession(), "1", REGION)  # type: ignore[arg-type]
    assert {c.status for c in coverage} == {CoverageStatus.DENIED}
    assert raw.instances == []


@pytest.mark.parametrize(
    ("platform", "image_name", "expected"),
    [
        (
            "Linux/UNIX",
            "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-20240101",
            {
                "os_family": "linux",
                "os_distribution": "ubuntu",
                "os_version": "22.04",
                "license_model": "none",
            },
        ),
        (
            "Red Hat Enterprise Linux",
            "RHEL-9.4.0_HVM-20240101-x86_64-0-Hourly2-GP3",
            {
                "os_family": "linux",
                "os_distribution": "rhel",
                "os_version": "9.4",
                "license_model": "included",
            },
        ),
        (
            "Windows",
            "Windows_Server-2019-English-Full-Base-2024.01.10",
            {
                "os_family": "windows",
                "os_distribution": "windows-server",
                "os_version": "2019",
                "license_model": "included",
            },
        ),
        (
            "Windows",
            "Windows_Server-2012-R2_RTM-English-64Bit-Base",
            {"os_family": "windows", "os_version": "2012 R2"},
        ),
        (
            "Linux/UNIX",
            "al2023-ami-2023.5.20240805.0-kernel-6.1-x86_64",
            {"os_distribution": "amazon-linux", "os_version": "2023"},
        ),
        ("", None, {"os_family": "unknown", "license_model": "unknown"}),
    ],
)
def test_infer_os(platform: str, image_name: str | None, expected: dict[str, str]) -> None:
    inst = {"PlatformDetails": platform, "Platform": "windows" if platform == "Windows" else None}
    out = infer_os(inst, {"Name": image_name} if image_name else None)
    for k, v in expected.items():
        assert out.get(k) == v, (k, out)


def test_instance_store_is_flagged_ephemeral() -> None:
    raw = RegionRaw(
        account="1",
        region=REGION,
        instances=[
            {
                "InstanceId": "i-1",
                "InstanceType": "i4i.large",
                "State": {"Name": "running"},
                "Architecture": "x86_64",
                "BlockDeviceMappings": [],
            }
        ],
        instance_types={
            "i4i.large": {
                "VCpuInfo": {"DefaultVCpus": 2},
                "MemoryInfo": {"SizeInMiB": 16384},
                "InstanceStorageInfo": {"TotalSizeInGB": 468, "Disks": [{"Type": "ssd"}]},
            }
        },
    )
    vm = normalize_region(uuid.uuid4(), raw).resources[0]
    assert vm.spec["local_storage_gib"] == 468
    assert vm.spec["disks"] == [
        {
            "device": "instance-store",
            "size_gib": 468,
            "type_class": "instance-store-ssd",
            "boot": False,
            "encrypted": False,
            "ephemeral": True,
        }
    ]


def test_family_is_inferred_from_distribution_when_platform_unknown() -> None:
    out = infer_os({}, {"Name": "ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-server"})
    assert (out["os_family"], out["provenance"]["os_family"]) == ("linux", "inferred")
    assert out["license_model"] == "unknown"


@pytest.mark.parametrize(
    ("name", "distro", "version"),
    [
        ("debian-12-amd64-20240717-1811", "debian", "12"),
        ("CentOS-7-2111-20220825_1.x86_64", "centos", "7"),
        ("Rocky-9-EC2-Base-9.4-20240523.0.x86_64", "rocky", "9"),
        ("suse-sles-15-sp5-v20240129-hvm-ssd-x86_64", "sles", "15"),
    ],
)
def test_infer_os_versions_ignore_architecture(name: str, distro: str, version: str) -> None:
    out = infer_os({"PlatformDetails": "Linux/UNIX"}, {"Name": name})
    assert (out.get("os_distribution"), out.get("os_version")) == (distro, version)
