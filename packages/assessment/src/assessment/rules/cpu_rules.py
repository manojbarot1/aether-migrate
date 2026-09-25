"""CPU-001 — architecture compatibility rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


class CPU001Rule(AssessmentRule):
    """CPU-001 (blocker): No arm64 SKUs available in the target region.

    If the source VM uses arm64 architecture, checks whether the catalog
    contains any arm64-capable instance types in the target region.
    """

    rule_id = "CPU-001"
    severity = RuleSeverity.blocker
    applies_to = "both"
    title = "No arm64 instance types available in target region"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        arch = (source_vm.architecture or "").lower()
        if arch != "arm64":
            return None

        if catalog.has_arm64_in_region(target_provider, target_region):
            return None

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM uses arm64 architecture but no arm64 instance types are "
                f"available in {target_provider.value}/{target_region}."
            ),
            evidence={
                "cpu_arch": arch,
                "target_provider": target_provider.value,
                "target_region": target_region,
            },
            remediation=(
                "Choose a target region that supports arm64 instance types, or consider "
                "cross-compiling the workload to run on x86_64."
            ),
        )
