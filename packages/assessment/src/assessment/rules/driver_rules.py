"""DRV-001 — cloud-specific driver compatibility rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext

# AWS instance type prefixes that are known to use NVMe or ENA
_AWS_NVME_FAMILIES = frozenset([
    "i3", "i3en", "i4i", "c5d", "c6id", "m5d", "m6id", "r5d", "r6id",
    "z1d", "x2idn", "p3dn", "g4dn",
])


def _uses_nvme_or_ena(instance_type: str | None) -> bool:
    """Return True if the instance type is known to use NVMe or ENA drivers."""
    if not instance_type:
        return False
    prefix = instance_type.split(".")[0].lower()
    return prefix in _AWS_NVME_FAMILIES or "nvme" in instance_type.lower()


class DRV001Rule(AssessmentRule):
    """DRV-001 (warning): AWS NVMe/ENA drivers incompatible with Azure/GCP/IBM.

    When migrating from AWS to Azure (or other non-AWS targets), the AWS-specific
    NVMe and ENA drivers will not function. Hyper-V or VirtIO drivers are required.
    """

    rule_id = "DRV-001"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "AWS-specific drivers incompatible with target provider"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        if source_vm.provider != ProviderName.aws:
            return None

        if target_provider == ProviderName.aws:
            return None  # Same provider, no driver issue

        has_nvme = _uses_nvme_or_ena(source_vm.instance_type)
        # Also check tags for NVMe evidence
        tags_str = " ".join(str(v) for v in source_vm.tags.values()).lower()
        if not has_nvme and "nvme" not in tags_str and "ena" not in tags_str:
            # No direct evidence, but still warn for any AWS→non-AWS migration
            # as ENA is enabled on almost all modern AWS instances
            has_nvme = True  # ENA is present on all Nitro instances

        if not has_nvme:
            return None

        provider_specific = {
            ProviderName.azure: "Hyper-V drivers (virtio-net and virtio-scsi)",
            ProviderName.gcp: "VirtIO drivers",
            ProviderName.ibm: "VirtIO drivers",
        }
        required_drivers = provider_specific.get(target_provider, "VirtIO drivers")

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM runs on AWS (instance type: {source_vm.instance_type!r}) "
                f"and likely has NVMe/ENA drivers installed. These drivers will not "
                f"function on {target_provider.value}. {required_drivers} are required."
            ),
            evidence={
                "source_provider": source_vm.provider.value,
                "instance_type": source_vm.instance_type,
                "target_provider": target_provider.value,
            },
            remediation=(
                f"Before cutover: install {required_drivers} on the source VM, or use "
                "AWS Application Migration Service (MGN) which handles driver injection "
                "automatically during replication."
            ),
        )
