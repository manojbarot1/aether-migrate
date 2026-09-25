"""BOOT-001 and BOOT-002 — boot mode and partition table rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext

# Azure v5 series require UEFI (Gen2 VMs)
_AZURE_GEN2_SERIES = ("v5",)


def _is_azure_gen2_only(target_provider: ProviderName, catalog: CatalogContext) -> bool:
    """Return True if the target provider context only has Gen2 (UEFI-required) SKUs."""
    # Heuristic: if target is Azure, v5 series require UEFI. We check via catalog
    # whether UEFI-capable SKUs exist (any non-v5 SKU = BIOS capable option exists).
    return target_provider == ProviderName.azure and catalog.has_only_gen2_in_context()


class BOOT001Rule(AssessmentRule):
    """BOOT-001 (warning): Source uses BIOS but target may require UEFI.

    Azure Gen2 (v5 series) requires UEFI. If source boot_mode is "bios",
    flag as warning (conversion is possible). The ``boot_mode`` field is
    stored in VMSpec.extra["boot_mode"].
    """

    rule_id = "BOOT-001"
    severity = RuleSeverity.warning
    applies_to = "both"
    title = "BIOS boot mode may be incompatible with target generation"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        boot_mode = (source_vm.extra.get("boot_mode") or "").lower()

        if boot_mode not in ("bios", "legacy"):
            return None  # UEFI or unknown — not an issue

        if target_provider != ProviderName.azure:
            return None  # Rule currently scoped to Azure Gen2

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                "Source VM uses BIOS boot mode. Azure Gen2 VM sizes (v5 series) require "
                "UEFI. You must convert the disk before using Gen2 sizes, or target Gen1 sizes."
            ),
            evidence={"boot_mode": boot_mode, "target_provider": target_provider.value},
            remediation=(
                "Convert the OS disk from MBR to GPT and enable UEFI before migration, "
                "or target Gen1 Azure VM sizes (v3/v4 series) which support BIOS boot."
            ),
            docs_url=(
                "https://learn.microsoft.com/en-us/azure/virtual-machines/generation-2"
            ),
        )


class BOOT002Rule(AssessmentRule):
    """BOOT-002 (blocker): Boot disk larger than 2 TiB likely uses MBR.

    MBR partitioning supports a maximum disk size of 2 TiB. If the boot disk
    exceeds 2048 GiB on a Linux VM, flag as a blocker. This is a heuristic
    since we cannot inspect the partition table without an agent.
    """

    rule_id = "BOOT-002"
    severity = RuleSeverity.blocker
    applies_to = "source"
    title = "Boot disk exceeds 2 TiB — possible MBR partition limit"

    _MBR_LIMIT_GIB = 2048

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        os_name = (source_vm.os_name or "").lower()
        if "linux" not in os_name and "rhel" not in os_name and "ubuntu" not in os_name \
                and "debian" not in os_name and "centos" not in os_name \
                and "sles" not in os_name and "suse" not in os_name:
            # Only apply to Linux VMs (heuristic; Windows uses its own check)
            # If os_name is something clearly non-linux we skip
            if "windows" in os_name:
                return None
            # Unknown OS — still apply the size check conservatively
            # by only skipping explicitly non-Linux OSes

        boot_disks = [d for d in source_vm.disks if d.boot]
        if not boot_disks:
            # Fall back to root_volume_size_gib
            root_size = source_vm.root_volume_size_gib or 0
            if root_size <= self._MBR_LIMIT_GIB:
                return None
            disk_size = root_size
        else:
            boot_disk = boot_disks[0]
            disk_size = boot_disk.size_gib or 0
            if disk_size <= self._MBR_LIMIT_GIB:
                return None

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Boot disk is {disk_size:.0f} GiB which exceeds the 2048 GiB MBR limit. "
                "If the disk uses MBR partitioning, migration will fail."
            ),
            evidence={"boot_disk_size_gib": disk_size, "mbr_limit_gib": self._MBR_LIMIT_GIB},
            remediation=(
                "Verify partition table type (MBR vs GPT) using 'parted -l' or 'gdisk'. "
                "If MBR: convert to GPT before migration. Install grub-efi if needed."
            ),
        )
