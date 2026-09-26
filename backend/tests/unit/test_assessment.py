"""Every assessment rule: a triggering case, a clean case, and engine scoring semantics."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from aether.assessment.engine import RULES, Context, Readiness, Severity, assess, quota_needs
from aether.core.inventory import VmSpec
from aether.sizing.engine import Requirement, SizedCandidate, SizingResult, Strategy

TODAY = date(2026, 9, 26)


def sizing(ok: bool = True, family: str = "Dsv5", vcpu: int = 4) -> SizingResult:
    cands = (
        [
            SizedCandidate(
                sku="Standard_D4s_v5",
                family=family,
                vcpu=vcpu,
                memory_mib=16384,
                cpu_arch="x86_64",
                local_disk_gib=0,
                hourly_usd=0.23,
                spec_source="curated",
                reasons=[],
            )
        ]
        if ok
        else []
    )
    return SizingResult(
        strategy=Strategy.LIKE_FOR_LIKE,
        requirement=Requirement(vcpu=vcpu, memory_mib=16384, cpu_arch="x86_64", basis="allocation"),
        candidates=cands,
        warnings=[] if ok else ["no eligible target SKU: x"],
    )


CLEAN_VM: dict[str, Any] = {
    "source_sku": "m5.xlarge",
    "vcpu": 4,
    "memory_mib": 16384,
    "cpu_arch": "x86_64",
    "os_family": "linux",
    "os_distribution": "ubuntu",
    "os_version": "24.04",
    "license_model": "none",
    "boot_mode": "uefi",
    "tenancy": "default",
    "disks": [{"device": "/dev/sda1", "size_gib": 30, "type_class": "gp3", "boot": True}],
    "nics": [{"private_ips": ["10.0.0.5"]}],
}


def ctx(vm: dict[str, Any] | None = None, **kw: Any) -> Context:
    base = Context(
        vm=VmSpec.model_validate({**CLEAN_VM, **(vm or {})}),
        native_id="i-1",
        name="web-01",
        source_provider="aws",
        target_provider="azure",
        target_region="westeurope",
        sizing=sizing(),
        today=TODAY,
    )
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def ids(c: Context, severity: Severity | None = None) -> set[str]:
    return {f.rule_id for f in assess(c, "r1").findings if severity is None or f.severity == severity}


def test_clean_vm_has_only_expected_baseline_findings() -> None:
    a = assess(ctx(), "r1")
    # Every AWS→Azure VM gets driver + IP-change warnings and the boot-mode note; nothing else.
    assert {f.rule_id for f in a.findings} == {"DRV-001", "NET-001", "BOOT-001"}
    assert a.readiness == Readiness.READY_WITH_CHANGES
    assert a.score == 100 - 12 * 2


@pytest.mark.parametrize(
    ("vm", "rule", "severity"),
    [
        ({"os_family": "unknown", "os_distribution": None}, "OS-001", Severity.WARNING),
        ({"os_distribution": "ubuntu", "os_version": "12.04"}, "OS-001", Severity.BLOCKER),
        ({"os_distribution": "centos", "os_version": "7.9"}, "OS-002", Severity.WARNING),
        (
            {"os_distribution": "windows-server", "os_version": "2012 R2", "os_family": "windows"},
            "OS-002",
            Severity.WARNING,
        ),
        ({"os_distribution": "debian", "os_version": "11"}, "OS-002", Severity.WARNING),  # ended 2026-08-31
        ({"os_distribution": "ubuntu", "os_version": "22.04"}, "OS-002", Severity.INFO),  # ends within a year
        ({"os_distribution": "amazon-linux", "os_version": "2023"}, "OS-003", Severity.BLOCKER),
        ({"cpu_arch": "arm64"}, "CPU-001", Severity.INFO),
        ({"boot_mode": "unknown"}, "BOOT-001", Severity.INFO),
        (
            {"boot_mode": "bios", "disks": [{"device": "/dev/sda1", "size_gib": 3000, "boot": True}]},
            "BOOT-002",
            Severity.BLOCKER,
        ),
        ({"disks": [{"device": "/dev/sda1", "size_gib": 5000, "boot": True}]}, "BOOT-002", Severity.BLOCKER),
        ({"disks": [{"device": "nvme1", "size_gib": 400, "ephemeral": True}]}, "DISK-001", Severity.WARNING),
        ({"disks": [{"device": "/dev/sdf", "size_gib": 40000}]}, "DISK-002", Severity.BLOCKER),
        (
            {"disks": [{"device": "/dev/sdf", "size_gib": 400, "type_class": "io2", "iops": 50000}]},
            "DISK-002",
            Severity.WARNING,
        ),
        (
            {"disks": [{"device": "/dev/sdf", "size_gib": 40, "kms_key_ref": "arn:aws:kms:..."}]},
            "DISK-003",
            Severity.WARNING,
        ),
        ({"nics": [{"private_ips": ["10.0.0.5"], "public_ips": ["3.3.3.3"]}]}, "NET-001", Severity.INFO),
        ({"instance_identity": "arn:aws:iam::1:instance-profile/app"}, "ID-001", Severity.WARNING),
        (
            {"license_model": "included", "platform_details": "Red Hat Enterprise Linux"},
            "LIC-001",
            Severity.INFO,
        ),
        ({"tenancy": "dedicated"}, "TEN-001", Severity.WARNING),
        ({"gpu_count": 1, "gpu_model": "NVIDIA T4"}, "GPU-001", Severity.BLOCKER),
    ],
)
def test_rule_triggers(vm: dict[str, Any], rule: str, severity: Severity) -> None:
    findings = [f for f in assess(ctx(vm), "r1").findings if f.rule_id == rule]
    assert findings, f"{rule} did not fire"
    assert severity in {f.severity for f in findings}
    assert all(f.remediation and f.message for f in findings)


def test_arm64_without_target_size_is_blocked() -> None:
    c = ctx({"cpu_arch": "arm64"}, sizing=sizing(ok=False))
    assert "CPU-001" in ids(c, Severity.BLOCKER)
    assert "SIZE-001" not in ids(c)  # the arm-specific rule explains it; no duplicate blocker


def test_no_size_for_x86_is_blocked() -> None:
    assert "SIZE-001" in ids(ctx(sizing=sizing(ok=False)), Severity.BLOCKER)


def test_security_group_rules() -> None:
    sgs = [
        {
            "native_id": "sg-web",
            "name": "web",
            "rules": [
                {
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port_from": 443,
                    "port_to": 443,
                    "peer_group_native_id": "sg-lb",
                },
                {
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port_from": 22,
                    "port_to": 22,
                    "peer_cidr": "0.0.0.0/0",
                },
            ],
        }
    ]
    found = {f.rule_id: f for f in assess(ctx(security_groups=sgs), "r1").findings}
    assert found["NET-002"].evidence["referenced_groups"] == ["sg-lb"]
    assert found["NET-003"].evidence["exposed"] == ["SSH (22) via web"]
    internal = [
        {
            "native_id": "sg",
            "rules": [
                {
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port_from": 22,
                    "port_to": 22,
                    "peer_cidr": "10.0.0.0/8",
                }
            ],
        }
    ]
    assert "NET-003" not in ids(ctx(security_groups=internal))


def test_load_balancer_membership() -> None:
    lbs = [{"native_id": "arn:...:loadbalancer/app/web/1", "name": "web-alb", "kind": "application"}]
    assert "DEP-001" in ids(ctx(load_balancers=lbs), Severity.WARNING)


def test_acknowledgement_clears_warnings_but_never_blockers() -> None:
    c = ctx({"instance_identity": "arn:x", "os_distribution": "amazon-linux", "os_version": "2"})
    acks = {
        rid: {"reason": "reviewed", "by": "a@b", "at": "t"}
        for rid in ("ID-001", "OS-003", "DRV-001", "NET-001")
    }
    a = assess(c, "r1", acks)
    by = {f.rule_id: f for f in a.findings}
    assert by["ID-001"].acknowledged
    assert not by["OS-003"].acknowledged  # blocker
    assert a.readiness == Readiness.BLOCKED
    assert a.score == 0
    clean = assess(ctx(), "r1", {"DRV-001": acks["DRV-001"], "NET-001": acks["NET-001"]})
    assert (clean.readiness, clean.score) == (Readiness.READY, 100)


def test_findings_are_ordered_and_rules_unique() -> None:
    a = assess(ctx({"gpu_count": 1, "gpu_model": "T4", "tenancy": "dedicated"}), "r1")
    order = [f.severity for f in a.findings]
    rank = {Severity.BLOCKER: 0, Severity.WARNING: 1, Severity.INFO: 2}
    assert order == sorted(order, key=rank.__getitem__)
    assert len({r.id for r in RULES}) == len(RULES)


def test_quota_needs_aggregate_per_family() -> None:
    q = quota_needs(
        [
            ("a", sizing(family="Dsv5", vcpu=4)),
            ("b", sizing(family="Dsv5", vcpu=8)),
            ("c", sizing(family="Esv5", vcpu=16)),
            ("d", sizing(ok=False)),
        ]
    )
    assert q == [{"family": "Esv5", "vcpu": 16}, {"family": "Dsv5", "vcpu": 12}]
