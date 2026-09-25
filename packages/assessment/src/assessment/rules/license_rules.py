"""LIC-001 — software licensing flag rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext

_LICENSED_SOFTWARE_HINTS = [
    "sql server", "sqlserver", "mssql",
    "rhel", "red hat",
    "sles", "suse",
    "oracle",
]


def _has_licensed_software(source_vm: VMSpec) -> str | None:
    """Return the first detected licensed software name, or None."""
    os_name_lower = (source_vm.os_name or "").lower()
    tags_combined = " ".join(
        f"{k} {v}" for k, v in source_vm.tags.items()
    ).lower()
    combined = f"{os_name_lower} {tags_combined}"

    for hint in _LICENSED_SOFTWARE_HINTS:
        if hint in combined:
            return hint
    return None


class LIC001Rule(AssessmentRule):
    """LIC-001 (warning): Commercial software licensing requires review.

    Windows, RHEL, SLES, SQL Server, and Oracle carry per-use licences that
    may not transfer to a new cloud. This rule flags for review only —
    it never computes BYOL eligibility.
    """

    rule_id = "LIC-001"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "Software licensing requires review before migration"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        os_name = (source_vm.os_name or "").lower()
        is_windows = "windows" in os_name

        licensed_sw = _has_licensed_software(source_vm)

        if not is_windows and not licensed_sw:
            return None

        if is_windows:
            software_note = "Windows Server"
        else:
            software_note = licensed_sw or "licensed software"

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM runs {software_note!r} which carries commercial licensing. "
                "Licensing terms may change when moving between cloud providers. "
                "Review licence portability before migration."
            ),
            evidence={
                "os_name": source_vm.os_name,
                "detected_software": software_note,
                "target_provider": target_provider.value,
            },
            remediation=(
                "Consult your licence agreement for cloud portability. "
                "Options include: Licence Included (pay-as-you-go on target), "
                "Azure Hybrid Benefit / AWS BYOL / GCP Sole Tenant for existing licences. "
                "Engage your Microsoft/Red Hat/Oracle account team before migration."
            ),
        )
