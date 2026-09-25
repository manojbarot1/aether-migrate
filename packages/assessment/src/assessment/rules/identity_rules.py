"""ID-001 — cloud identity and IAM role migration rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


class ID001Rule(AssessmentRule):
    """ID-001 (warning): Cloud-specific identity reference will break after migration.

    AWS IAM instance profiles, Azure managed identities, and GCP service
    accounts are cloud-specific. Any application relying on the VM's attached
    identity to access cloud APIs will break when migrated.
    """

    rule_id = "ID-001"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "Cloud-specific identity reference will not migrate"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        identity_ref: str | None = source_vm.extra.get("identity_ref")

        if not identity_ref:
            return None

        source_identity_type = {
            ProviderName.aws: "IAM instance profile",
            ProviderName.azure: "managed identity",
            ProviderName.gcp: "service account",
            ProviderName.ibm: "instance identity",
        }.get(source_vm.provider, "cloud identity")

        target_identity_type = {
            ProviderName.azure: "Azure managed identity",
            ProviderName.aws: "IAM instance profile",
            ProviderName.gcp: "GCP service account",
            ProviderName.ibm: "IBM trusted profile",
        }.get(target_provider, "target cloud identity")

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM has an attached {source_identity_type} ({identity_ref!r}). "
                "Cloud API calls using this identity will fail after migration."
            ),
            evidence={
                "identity_ref": identity_ref,
                "source_provider": source_vm.provider.value,
                "source_identity_type": source_identity_type,
            },
            remediation=(
                f"Create an equivalent {target_identity_type} on the target and assign "
                "the necessary permissions. Update application configuration to use the "
                "new identity. Test all cloud API calls after migration."
            ),
        )
