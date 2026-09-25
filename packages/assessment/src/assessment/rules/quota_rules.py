"""QUOTA-001 — vCPU quota check rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


def _infer_vcpu_family(target_provider: ProviderName, source_vm: VMSpec) -> str:
    """Infer the vCPU family/quota group for the target provider.

    This is necessarily a heuristic since we do not know the exact target
    SKU at assessment time. We use a generic family name per provider.
    """
    if target_provider == ProviderName.azure:
        arch = (source_vm.architecture or "").lower()
        if arch == "arm64":
            return "standardDPSv5Family"
        return "standardDSv5Family"
    if target_provider == ProviderName.aws:
        return "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances"
    if target_provider == ProviderName.gcp:
        return "CPUS"
    return "vcpus"


class QUOTA001Rule(AssessmentRule):
    """QUOTA-001 (blocker/warning): Insufficient vCPU quota in target region.

    If the catalog reports a quota below the required vCPU count, fires as a
    blocker. If quota data is unavailable, fires as a warning.
    """

    rule_id = "QUOTA-001"
    severity = RuleSeverity.blocker  # default; overridden in check() when unknown
    applies_to = "target"
    title = "Insufficient vCPU quota in target region"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        vcpu_needed = source_vm.vcpu
        if not vcpu_needed:
            return None

        vcpu_family = _infer_vcpu_family(target_provider, source_vm)
        quota = catalog.get_quota(target_provider, target_region, vcpu_family)

        if quota is None:
            # Unknown quota — downgrade to warning
            return FindingRecord(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                severity=RuleSeverity.warning,
                applies_to=self.applies_to,
                title="vCPU quota unknown for target region — verify before migration",
                message=(
                    f"Could not determine vCPU quota for {vcpu_family!r} in "
                    f"{target_provider.value}/{target_region}. "
                    f"The source VM requires {vcpu_needed} vCPUs."
                ),
                evidence={
                    "vcpu_needed": vcpu_needed,
                    "vcpu_family": vcpu_family,
                    "target_provider": target_provider.value,
                    "target_region": target_region,
                    "quota_available": None,
                },
                remediation=(
                    f"Check your {target_provider.value} quota for {vcpu_family!r} in "
                    f"{target_region} and request an increase if needed before migration."
                ),
            )

        if quota >= vcpu_needed:
            return None

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"vCPU quota for {vcpu_family!r} in {target_provider.value}/{target_region} "
                f"is {quota}, but {vcpu_needed} vCPUs are required."
            ),
            evidence={
                "vcpu_needed": vcpu_needed,
                "vcpu_family": vcpu_family,
                "quota_available": quota,
                "target_provider": target_provider.value,
                "target_region": target_region,
            },
            remediation=(
                f"Request a vCPU quota increase for {vcpu_family!r} in {target_region} "
                f"via the {target_provider.value} portal or support ticket before migrating."
            ),
        )
