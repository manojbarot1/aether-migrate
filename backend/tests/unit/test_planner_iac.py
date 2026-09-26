"""Planner determinism, wave grouping, hashing; OpenTofu rule translation."""

from __future__ import annotations

from typing import Any

from aether.iac.azure import fmt_hcl, render, translate_rules
from aether.planner.engine import PlanOptions, PlanVm, build_plan, canonical_hash, to_markdown


def vm(
    nid: str,
    lbs: list[str] | None = None,
    readiness: str = "ready_with_changes",
    gib: int = 100,
    findings: list[dict[str, Any]] | None = None,
    family: str = "Dsv5",
    vcpu: int = 4,
) -> PlanVm:
    return PlanVm(
        resource_id=f"r-{nid}",
        native_id=nid,
        name=nid,
        source_sku="m5.xlarge",
        os_family="linux",
        os="ubuntu 22.04",
        vcpu=vcpu,
        memory_mib=16384,
        target_sku="Standard_D4s_v5",
        target_family=family,
        disks_gib=gib,
        subnet="subnet-a",
        security_groups=["sg-web"],
        load_balancers=lbs or [],
        readiness=readiness,
        open_findings=findings or [],
        monthly_usd={"on_demand": 200.0, "reserved_1y": 130.0, "reserved_3y": 90.0},
        one_time_usd=20.0,
    )


NETWORKS = [{"native_id": "vpc-1", "name": "prod", "cidrs": ["10.20.0.0/16"]}]


def plan(vms: list[PlanVm], **opts: Any) -> Any:
    return build_plan(
        vms,
        [],
        source={"provider": "aws"},
        target={"provider": "azure", "region": "westeurope"},
        options=PlanOptions(**opts),
        networks=NETWORKS,
        inputs={"assessment_run_id": "x"},
    )


def test_load_balanced_pools_share_a_wave_and_go_after_standalone() -> None:
    p = plan([vm("web-1", ["alb"]), vm("web-2", ["alb"]), vm("db-1"), vm("batch-1", readiness="ready")])
    assert [w.vms for w in p.waves] == [["batch-1", "db-1"], ["web-1", "web-2"]]
    assert "load balancer" in p.waves[1].reason
    assert "load balancer" in p.waves[1].steps[3].title.lower()


def test_standalone_machines_batch_by_ten() -> None:
    p = plan([vm(f"app-{i:02d}") for i in range(23)])
    assert [len(w.vms) for w in p.waves] == [10, 10, 3]


def test_plan_is_deterministic_and_hash_sensitive() -> None:
    a = plan([vm("b"), vm("a")])
    b = plan([vm("a"), vm("b")])
    assert canonical_hash(a) == canonical_hash(b)
    assert canonical_hash(plan([vm("a"), vm("b")], replication_bandwidth_mbps=100)) != canonical_hash(a)


def test_timing_quota_and_totals() -> None:
    p = plan([vm("a", gib=450), vm("b", gib=0, family="Esv5", vcpu=8)], replication_bandwidth_mbps=500)
    # 450 GiB * 8 * 1024 Mb / 500 Mbps = 7372.8 s = 2.0 h
    assert p.waves[0].initial_sync_hours == 2.0
    quota = next(x for x in p.prerequisites if x["id"] == "PRE-2")["evidence"]["per_family_vcpu"]
    assert quota == {"Dsv5": 4, "Esv5": 8}
    assert p.totals["target_monthly_usd"]["on_demand"] == 400.0
    assert p.totals["one_time_usd"] == 40.0


def test_blockers_and_identity_prerequisite() -> None:
    p = plan(
        [
            vm("a", findings=[{"rule_id": "ID-001", "severity": "warning", "title": "role"}]),
            vm("b", findings=[{"rule_id": "OS-003", "severity": "blocker", "title": "al"}]),
        ]
    )
    assert p.totals["open_blockers"] == ["b:OS-003"]
    assert any(x["id"] == "PRE-5" and x["evidence"]["machines"] == ["a"] for x in p.prerequisites)


def test_cold_image_mechanism_changes_steps_and_downtime() -> None:
    p = plan([vm("a", gib=1000)], mechanism="cold_image", replication_bandwidth_mbps=1000)
    assert "Snapshot" in p.waves[0].steps[1].title
    assert p.waves[0].cutover_downtime_minutes > 30
    md = to_markdown("Pilot", 1, "f" * 64, p)
    assert md.startswith("# Migration plan: Pilot (v1)")
    assert "| W1.2 Snapshot" in md


def test_rule_translation() -> None:
    sg = {
        "rules": [
            {
                "direction": "ingress",
                "protocol": "tcp",
                "port_from": 443,
                "port_to": 443,
                "peer_cidr": "0.0.0.0/0",
            },
            {
                "direction": "ingress",
                "protocol": "tcp",
                "port_from": 8080,
                "port_to": 8080,
                "peer_group_native_id": "sg-lb",
            },
            {"direction": "ingress", "protocol": "all", "peer_cidr": "10.0.0.0/8"},
            {
                "direction": "ingress",
                "protocol": "tcp",
                "port_from": 80,
                "port_to": 80,
                "peer_prefix_list": "pl-1",
            },
            {"direction": "egress", "protocol": "all", "peer_cidr": "0.0.0.0/0"},
        ]
    }
    blocks, notes = translate_rules(sg, {"sg-lb": "asg_lb"})
    text = "\n".join(blocks)
    assert text.count("security_rule {") == 3  # default egress skipped, prefix list noted
    assert 'destination_port_range = "443"' in text
    assert 'source_address_prefix = "*"' in text  # 0.0.0.0/0
    assert "source_application_security_group_ids = [azurerm_application_security_group.asg_lb.id]" in text
    assert 'protocol = "*"' in text
    assert notes == [
        "ingress rule to AWS prefix list pl-1 has no Azure equivalent; replace with a service tag"
    ]
    restricted, _ = translate_rules(
        {
            "rules": [
                {
                    "direction": "egress",
                    "protocol": "tcp",
                    "port_from": 5432,
                    "port_to": 5432,
                    "peer_cidr": "10.1.0.0/16",
                }
            ]
        },
        {},
    )
    assert any('access = "Deny"' in b and "4096" in b for b in restricted)


def test_render_merges_multiple_security_groups_per_machine() -> None:
    files, _ = render(
        plan_name="p",
        region="westeurope",
        resource_group="rg",
        networks=NETWORKS,
        subnets=[
            {"native_id": "subnet-a", "name": "a", "cidr": "10.20.1.0/24", "network_native_id": "vpc-1"}
        ],
        security_groups=[
            {
                "native_id": "sg-web",
                "name": "web",
                "rules": [
                    {
                        "direction": "ingress",
                        "protocol": "tcp",
                        "port_from": 80,
                        "port_to": 80,
                        "peer_cidr": "0.0.0.0/0",
                    }
                ],
            },
            {
                "native_id": "sg-admin",
                "name": "admin",
                "rules": [
                    {
                        "direction": "ingress",
                        "protocol": "tcp",
                        "port_from": 22,
                        "port_to": 22,
                        "peer_cidr": "10.0.0.0/8",
                    }
                ],
            },
        ],
        vm_bindings=[
            {"native_id": "i-1", "subnet": "subnet-a", "security_groups": ["sg-web", "sg-admin"]},
            {"native_id": "i-2", "subnet": "subnet-a", "security_groups": ["sg-admin"]},
        ],
    )
    main = files["main.tf"]
    assert main.count('resource "azurerm_network_security_group"') == 3  # web, admin, merged(web+admin)
    assert "# Union of sg-admin, sg-web" in main
    assert "nsg_id      = azurerm_network_security_group.nsg_merged_" in files["outputs.tf"]
    assert set(files) == {"versions.tf", "variables.tf", "main.tf", "outputs.tf", "README.md"}


def test_fmt_aligns_attributes_like_tofu() -> None:
    assert fmt_hcl("  a = 1\n  long_name = 2\n\n  b = {\n    c = 3\n  }") == (
        "  a         = 1\n  long_name = 2\n\n  b = {\n    c = 3\n  }"
    )
