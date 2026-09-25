"""DISK-001 and DISK-002 — disk migration and capacity rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


class DISK001Rule(AssessmentRule):
    """DISK-001 (warning): Ephemeral disks will not be migrated.

    Instance-store (AWS) or temp disks (Azure) are ephemeral and their
    contents do not persist. Data on these disks will be lost at migration.
    """

    rule_id = "DISK-001"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "Ephemeral disk data will not be migrated"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        ephemeral_disks = [d for d in source_vm.disks if d.ephemeral]
        if not ephemeral_disks:
            return None

        disk_details = [
            {"type_class": d.type_class, "size_gib": d.size_gib}
            for d in ephemeral_disks
        ]

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM has {len(ephemeral_disks)} ephemeral disk(s). Data stored on "
                "instance-store or temp disks will not be migrated and will be lost."
            ),
            evidence={"ephemeral_disk_count": len(ephemeral_disks), "disks": disk_details},
            remediation=(
                "Ensure no application data is stored exclusively on ephemeral disks. "
                "Copy any required data to persistent block storage before migration."
            ),
        )


class DISK002Rule(AssessmentRule):
    """DISK-002 (blocker): Disk exceeds target provider's maximum size, IOPS, or throughput.

    Queries CatalogContext for disk class limits and flags any disk that
    exceeds the target maximum.
    """

    rule_id = "DISK-002"
    severity = RuleSeverity.blocker
    applies_to = "target"
    title = "Disk exceeds target provider maximum size, IOPS, or throughput"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        violations: list[dict] = []

        for idx, disk in enumerate(source_vm.disks):
            if disk.ephemeral:
                continue  # ephemeral disks are not migrated (DISK-001 covers this)

            disk_type = disk.type_class or "standard"
            limits = catalog.get_disk_limits(target_provider, disk_type)
            if limits is None:
                continue  # No catalog data — can't evaluate

            exceeded: dict[str, object] = {}

            if disk.size_gib is not None and limits.max_size_gib is not None:
                if disk.size_gib > limits.max_size_gib:
                    exceeded["size_gib"] = {
                        "actual": disk.size_gib, "max": limits.max_size_gib
                    }

            if disk.iops is not None and limits.max_iops is not None:
                if disk.iops > limits.max_iops:
                    exceeded["iops"] = {"actual": disk.iops, "max": limits.max_iops}

            if disk.throughput_mbps is not None and limits.max_throughput_mbps is not None:
                if disk.throughput_mbps > limits.max_throughput_mbps:
                    exceeded["throughput_mbps"] = {
                        "actual": disk.throughput_mbps,
                        "max": limits.max_throughput_mbps,
                    }

            if exceeded:
                violations.append({
                    "disk_index": idx,
                    "disk_type": disk_type,
                    "exceeded": exceeded,
                })

        if not violations:
            return None

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"{len(violations)} disk(s) exceed target provider limits for "
                f"{target_provider.value}/{target_region}."
            ),
            evidence={
                "target_provider": target_provider.value,
                "target_region": target_region,
                "violations": violations,
            },
            remediation=(
                "Split large disks into multiple smaller volumes, or choose a target disk "
                "type with higher limits. Check the target provider's disk documentation "
                "for the maximum supported size/IOPS/throughput."
            ),
        )
