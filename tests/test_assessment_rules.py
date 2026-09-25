"""Unit tests for all 16 assessment rules.

Every rule has at least 2 test cases: one that fires (returns a FindingRecord)
and one that passes (returns None).

Tests are pure-unit — no DB required. A mock CatalogContext is used.
"""

from __future__ import annotations

import uuid
from typing import Any

from assessment.engine import CatalogContext, DiskLimits
from assessment.models import RuleSeverity
from core.models import (
    DiskSpec,
    EdgeKind,
    NicSpec,
    ProviderName,
    ResourceEdge,
    VMSpec,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WORKSPACE_ID = uuid.UUID("aaaa0000-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("bbbb0000-0000-0000-0000-000000000001")


def make_vm(
    *,
    os_name: str = "Ubuntu",
    os_version: str = "22.04",
    architecture: str = "x86_64",
    vcpu: int = 4,
    memory_gib: float = 16.0,
    instance_type: str = "m5.xlarge",
    provider: ProviderName = ProviderName.aws,
    disks: list[DiskSpec] | None = None,
    nics: list[NicSpec] | None = None,
    tags: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    region: str = "us-east-1",
) -> VMSpec:
    """Build a VMSpec fixture."""
    return VMSpec(
        id=uuid.uuid4(),
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        provider=provider,
        native_id="i-test001",
        account="123456789",
        region=region,
        vcpu=vcpu,
        memory_gib=memory_gib,
        instance_type=instance_type,
        architecture=architecture,
        os_name=os_name,
        os_version=os_version,
        disks=disks or [],
        nics=nics or [],
        tags=tags or {},
        extra=extra or {},
    )


def make_catalog(
    *,
    arm64_regions: dict[str, set[str]] | None = None,
    quotas: dict[tuple[str, str, str], int] | None = None,
    disk_limits: dict[tuple[str, str], DiskLimits] | None = None,
    catalog_version: str = "2025-01",
    has_only_gen2: bool = False,
) -> CatalogContext:
    """Build a CatalogContext fixture (no DB needed)."""
    return CatalogContext(
        arm64_regions=arm64_regions or {},
        quotas=quotas or {},
        disk_limits=disk_limits or {},
        catalog_version=catalog_version,
        has_only_gen2=has_only_gen2,
    )


# ---------------------------------------------------------------------------
# OS-001
# ---------------------------------------------------------------------------


class TestOS001:
    def test_fires_for_unsupported_windows_version(self) -> None:
        from assessment.rules.os_rules import OS001Rule

        vm = make_vm(os_name="Windows Server", os_version="2003")
        rule = OS001Rule()
        finding = rule.check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "OS-001"
        assert finding.severity == RuleSeverity.blocker
        assert "2003" in finding.evidence["os_version"]

    def test_passes_for_supported_windows_version(self) -> None:
        from assessment.rules.os_rules import OS001Rule

        vm = make_vm(os_name="Windows Server", os_version="2022")
        finding = OS001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None

    def test_fires_for_unsupported_linux_distro(self) -> None:
        from assessment.rules.os_rules import OS001Rule

        vm = make_vm(os_name="Gentoo Linux", os_version="2.0")
        finding = OS001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.severity == RuleSeverity.blocker

    def test_passes_for_supported_rhel(self) -> None:
        from assessment.rules.os_rules import OS001Rule

        vm = make_vm(os_name="Red Hat Enterprise Linux", os_version="9.2")
        finding = OS001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None

    def test_skips_when_no_os_name(self) -> None:
        from assessment.rules.os_rules import OS001Rule

        vm = make_vm(os_name="", os_version="")
        finding = OS001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# OS-002
# ---------------------------------------------------------------------------


class TestOS002:
    def test_fires_for_eol_windows_2012(self) -> None:
        from assessment.rules.os_rules import OS002Rule

        vm = make_vm(os_name="Windows Server", os_version="2012 R2")
        finding = OS002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "OS-002"
        assert finding.severity == RuleSeverity.warning
        assert "2023" in finding.evidence["eol_date"] or "2012" in finding.evidence["os_version"]

    def test_passes_for_supported_windows_2022(self) -> None:
        from assessment.rules.os_rules import OS002Rule

        vm = make_vm(os_name="Windows Server", os_version="2022")
        finding = OS002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None  # 2022 EOL is in 2031

    def test_passes_when_no_eol_data(self) -> None:
        from assessment.rules.os_rules import OS002Rule

        vm = make_vm(os_name="ExoticLinux", os_version="5.0")
        finding = OS002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None  # Unknown EOL — don't flag


# ---------------------------------------------------------------------------
# CPU-001
# ---------------------------------------------------------------------------


class TestCPU001:
    def test_fires_for_arm64_when_no_arm64_in_region(self) -> None:
        from assessment.rules.cpu_rules import CPU001Rule

        vm = make_vm(architecture="arm64")
        catalog = make_catalog(arm64_regions={})  # No arm64 SKUs anywhere
        finding = CPU001Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is not None
        assert finding.rule_id == "CPU-001"
        assert finding.severity == RuleSeverity.blocker
        assert finding.evidence["cpu_arch"] == "arm64"

    def test_passes_for_arm64_when_arm64_available_in_region(self) -> None:
        from assessment.rules.cpu_rules import CPU001Rule

        vm = make_vm(architecture="arm64")
        catalog = make_catalog(arm64_regions={"azure": {"eastus"}})
        finding = CPU001Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is None

    def test_passes_for_x86_64_always(self) -> None:
        from assessment.rules.cpu_rules import CPU001Rule

        vm = make_vm(architecture="x86_64")
        catalog = make_catalog(arm64_regions={})
        finding = CPU001Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is None


# ---------------------------------------------------------------------------
# BOOT-001
# ---------------------------------------------------------------------------


class TestBOOT001:
    def test_fires_for_bios_targeting_azure(self) -> None:
        from assessment.rules.boot_rules import BOOT001Rule

        vm = make_vm(extra={"boot_mode": "bios"})
        finding = BOOT001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "BOOT-001"
        assert finding.severity == RuleSeverity.warning
        assert finding.evidence["boot_mode"] == "bios"

    def test_passes_for_uefi(self) -> None:
        from assessment.rules.boot_rules import BOOT001Rule

        vm = make_vm(extra={"boot_mode": "uefi"})
        finding = BOOT001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None

    def test_passes_for_bios_targeting_aws(self) -> None:
        from assessment.rules.boot_rules import BOOT001Rule

        vm = make_vm(extra={"boot_mode": "bios"})
        finding = BOOT001Rule().check(vm, ProviderName.aws, "us-east-1", make_catalog())

        assert finding is None  # Rule scoped to Azure only


# ---------------------------------------------------------------------------
# BOOT-002
# ---------------------------------------------------------------------------


class TestBOOT002:
    def test_fires_for_linux_boot_disk_over_2048_gib(self) -> None:
        from assessment.rules.boot_rules import BOOT002Rule

        vm = make_vm(
            os_name="Ubuntu",
            disks=[DiskSpec(size_gib=3000, boot=True)],
        )
        finding = BOOT002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "BOOT-002"
        assert finding.severity == RuleSeverity.blocker
        assert finding.evidence["boot_disk_size_gib"] == 3000

    def test_passes_for_boot_disk_under_2048_gib(self) -> None:
        from assessment.rules.boot_rules import BOOT002Rule

        vm = make_vm(
            os_name="Ubuntu",
            disks=[DiskSpec(size_gib=128, boot=True)],
        )
        finding = BOOT002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None

    def test_passes_for_windows_vm(self) -> None:
        from assessment.rules.boot_rules import BOOT002Rule

        vm = make_vm(
            os_name="Windows Server",
            disks=[DiskSpec(size_gib=4096, boot=True)],
        )
        finding = BOOT002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None  # Windows excluded from this heuristic


# ---------------------------------------------------------------------------
# DRV-001
# ---------------------------------------------------------------------------


class TestDRV001:
    def test_fires_for_aws_to_azure_migration(self) -> None:
        from assessment.rules.driver_rules import DRV001Rule

        vm = make_vm(provider=ProviderName.aws, instance_type="m5.xlarge")
        finding = DRV001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "DRV-001"
        assert finding.severity == RuleSeverity.warning
        assert finding.evidence["source_provider"] == "aws"

    def test_passes_for_aws_to_aws_migration(self) -> None:
        from assessment.rules.driver_rules import DRV001Rule

        vm = make_vm(provider=ProviderName.aws, instance_type="m5.xlarge")
        finding = DRV001Rule().check(vm, ProviderName.aws, "us-west-2", make_catalog())

        assert finding is None

    def test_passes_for_azure_source(self) -> None:
        from assessment.rules.driver_rules import DRV001Rule

        vm = make_vm(provider=ProviderName.azure, instance_type="Standard_D4s_v5")
        finding = DRV001Rule().check(vm, ProviderName.aws, "us-east-1", make_catalog())

        assert finding is None  # Only fires for AWS sources


# ---------------------------------------------------------------------------
# DISK-001
# ---------------------------------------------------------------------------


class TestDISK001:
    def test_fires_when_ephemeral_disk_present(self) -> None:
        from assessment.rules.disk_rules import DISK001Rule

        vm = make_vm(disks=[
            DiskSpec(size_gib=100, boot=True),
            DiskSpec(size_gib=50, ephemeral=True),
        ])
        finding = DISK001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "DISK-001"
        assert finding.severity == RuleSeverity.warning
        assert finding.evidence["ephemeral_disk_count"] == 1

    def test_passes_when_no_ephemeral_disks(self) -> None:
        from assessment.rules.disk_rules import DISK001Rule

        vm = make_vm(disks=[DiskSpec(size_gib=100, boot=True, ephemeral=False)])
        finding = DISK001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# DISK-002
# ---------------------------------------------------------------------------


class TestDISK002:
    def test_fires_when_disk_exceeds_max_size(self) -> None:
        from assessment.rules.disk_rules import DISK002Rule

        catalog = make_catalog(disk_limits={
            ("azure", "premium_lrs"): DiskLimits(max_size_gib=32767, max_iops=20000, max_throughput_mbps=900),
        })
        vm = make_vm(disks=[DiskSpec(size_gib=40000, type_class="premium_lrs", boot=True)])
        finding = DISK002Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is not None
        assert finding.rule_id == "DISK-002"
        assert finding.severity == RuleSeverity.blocker
        assert finding.evidence["violations"][0]["exceeded"]["size_gib"]["actual"] == 40000

    def test_passes_when_disk_within_limits(self) -> None:
        from assessment.rules.disk_rules import DISK002Rule

        catalog = make_catalog(disk_limits={
            ("azure", "premium_lrs"): DiskLimits(max_size_gib=32767, max_iops=20000, max_throughput_mbps=900),
        })
        vm = make_vm(disks=[DiskSpec(size_gib=1000, type_class="premium_lrs")])
        finding = DISK002Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is None

    def test_passes_when_no_catalog_data(self) -> None:
        from assessment.rules.disk_rules import DISK002Rule

        vm = make_vm(disks=[DiskSpec(size_gib=50000, type_class="unknown_type")])
        finding = DISK002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None  # No catalog data — can't evaluate


# ---------------------------------------------------------------------------
# NET-001
# ---------------------------------------------------------------------------


class TestNET001:
    def test_fires_when_vm_has_private_ips(self) -> None:
        from assessment.rules.network_rules import NET001Rule

        vm = make_vm(nics=[NicSpec(private_ips=["10.0.1.5"])])
        finding = NET001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "NET-001"
        assert finding.severity == RuleSeverity.warning
        assert finding.evidence["private_ip_count"] == 1

    def test_passes_when_no_private_ips(self) -> None:
        from assessment.rules.network_rules import NET001Rule

        vm = make_vm(nics=[])
        finding = NET001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# NET-002
# ---------------------------------------------------------------------------


class TestNET002:
    def test_fires_when_sg_peer_rules_present_and_target_is_azure(self) -> None:
        from assessment.rules.network_rules import NET002Rule

        vm = make_vm(extra={"security_group_peer_rule_count": 3})
        finding = NET002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "NET-002"
        assert finding.severity == RuleSeverity.warning
        assert finding.evidence["security_group_peer_rule_count"] == 3

    def test_passes_for_aws_to_aws(self) -> None:
        from assessment.rules.network_rules import NET002Rule

        vm = make_vm(provider=ProviderName.aws, extra={"security_group_peer_rule_count": 5})
        finding = NET002Rule().check(vm, ProviderName.aws, "us-east-1", make_catalog())

        assert finding is None  # Same provider — no translation needed

    def test_passes_when_no_peer_rules(self) -> None:
        from assessment.rules.network_rules import NET002Rule

        vm = make_vm(extra={"security_group_peer_rule_count": 0})
        finding = NET002Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# NET-003
# ---------------------------------------------------------------------------


class TestNET003:
    def test_fires_when_vm_has_public_ip(self) -> None:
        from assessment.rules.network_rules import NET003Rule

        vm = make_vm(nics=[NicSpec(public_ips=["1.2.3.4"])])
        finding = NET003Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "NET-003"
        assert finding.severity == RuleSeverity.info
        assert finding.evidence["public_ip_count"] == 1

    def test_fires_when_tags_contain_dns_hint(self) -> None:
        from assessment.rules.network_rules import NET003Rule

        vm = make_vm(tags={"dns_name": "myapp.example.com"})
        finding = NET003Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.evidence["has_dns_hint"] is True

    def test_passes_when_no_public_ip_or_dns(self) -> None:
        from assessment.rules.network_rules import NET003Rule

        vm = make_vm(nics=[NicSpec(private_ips=["10.0.0.1"])], tags={})
        finding = NET003Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# ID-001
# ---------------------------------------------------------------------------


class TestID001:
    def test_fires_when_identity_ref_set(self) -> None:
        from assessment.rules.identity_rules import ID001Rule

        vm = make_vm(extra={"identity_ref": "arn:aws:iam::123:instance-profile/my-role"})
        finding = ID001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "ID-001"
        assert finding.severity == RuleSeverity.warning
        assert "arn:aws" in finding.evidence["identity_ref"]

    def test_passes_when_no_identity_ref(self) -> None:
        from assessment.rules.identity_rules import ID001Rule

        vm = make_vm(extra={})
        finding = ID001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# LIC-001
# ---------------------------------------------------------------------------


class TestLIC001:
    def test_fires_for_windows_os(self) -> None:
        from assessment.rules.license_rules import LIC001Rule

        vm = make_vm(os_name="Windows Server", os_version="2022")
        finding = LIC001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "LIC-001"
        assert finding.severity == RuleSeverity.warning

    def test_fires_for_sql_server_in_tags(self) -> None:
        from assessment.rules.license_rules import LIC001Rule

        vm = make_vm(os_name="Windows Server", tags={"software": "sql server 2019"})
        finding = LIC001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None

    def test_passes_for_plain_linux(self) -> None:
        from assessment.rules.license_rules import LIC001Rule

        vm = make_vm(os_name="Ubuntu", os_version="22.04", tags={})
        finding = LIC001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None


# ---------------------------------------------------------------------------
# QUOTA-001
# ---------------------------------------------------------------------------


class TestQUOTA001:
    def test_fires_as_blocker_when_quota_exceeded(self) -> None:
        from assessment.rules.quota_rules import QUOTA001Rule

        catalog = make_catalog(quotas={
            ("azure", "eastus", "standardDSv5Family"): 8,
        })
        vm = make_vm(vcpu=16)
        finding = QUOTA001Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is not None
        assert finding.rule_id == "QUOTA-001"
        assert finding.severity == RuleSeverity.blocker
        assert finding.evidence["vcpu_needed"] == 16
        assert finding.evidence["quota_available"] == 8

    def test_fires_as_warning_when_quota_unknown(self) -> None:
        from assessment.rules.quota_rules import QUOTA001Rule

        vm = make_vm(vcpu=8)
        finding = QUOTA001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.severity == RuleSeverity.warning
        assert finding.evidence["quota_available"] is None

    def test_passes_when_quota_sufficient(self) -> None:
        from assessment.rules.quota_rules import QUOTA001Rule

        catalog = make_catalog(quotas={
            ("azure", "eastus", "standardDSv5Family"): 100,
        })
        vm = make_vm(vcpu=16)
        finding = QUOTA001Rule().check(vm, ProviderName.azure, "eastus", catalog)

        assert finding is None


# ---------------------------------------------------------------------------
# AGENT-001
# ---------------------------------------------------------------------------


class TestAGENT001:
    def test_fires_when_ssm_detected_in_tags(self) -> None:
        from assessment.rules.agent_rules import AGENT001Rule

        vm = make_vm(
            os_name="Amazon Linux 2",
            tags={"installed_software": "amazon-ssm cloudwatch"},
        )
        finding = AGENT001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "AGENT-001"
        assert finding.severity == RuleSeverity.info

    def test_passes_when_no_agents_detected(self) -> None:
        from assessment.rules.agent_rules import AGENT001Rule

        vm = make_vm(os_name="Ubuntu", tags={"app": "nginx"})
        finding = AGENT001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None

    def test_skips_windows_vms(self) -> None:
        from assessment.rules.agent_rules import AGENT001Rule

        vm = make_vm(os_name="Windows Server", tags={"installed_software": "amazon-ssm"})
        finding = AGENT001Rule().check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None  # Windows excluded by design


# ---------------------------------------------------------------------------
# DEP-001
# ---------------------------------------------------------------------------


class TestDEP001:
    def test_fires_when_vm_is_behind_lb(self) -> None:
        from assessment.rules.dependency_rules import DEP001Rule

        vm = make_vm()
        lb_id = uuid.uuid4()
        edges = [
            ResourceEdge(
                from_id=vm.id,
                to_id=lb_id,
                kind=EdgeKind.behind_lb,
                workspace_id=WORKSPACE_ID,
            )
        ]
        rule = DEP001Rule(edges=edges)
        finding = rule.check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert finding.rule_id == "DEP-001"
        assert finding.severity == RuleSeverity.warning
        assert any(d["type"] == "behind_lb" for d in finding.evidence["dependencies"])

    def test_passes_when_no_topology_dependencies(self) -> None:
        from assessment.rules.dependency_rules import DEP001Rule

        vm = make_vm()
        rule = DEP001Rule(edges=[])
        finding = rule.check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is None

    def test_fires_when_vm_has_shared_disk(self) -> None:
        from assessment.rules.dependency_rules import DEP001Rule

        vm = make_vm()
        disk_id = uuid.uuid4()
        edges = [
            ResourceEdge(
                from_id=vm.id,
                to_id=disk_id,
                kind=EdgeKind.attached_to,
                workspace_id=WORKSPACE_ID,
            )
        ]
        rule = DEP001Rule(edges=edges)
        finding = rule.check(vm, ProviderName.azure, "eastus", make_catalog())

        assert finding is not None
        assert any(d["type"] == "shared_disk" for d in finding.evidence["dependencies"])
