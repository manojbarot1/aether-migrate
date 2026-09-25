"""AGENT-001 — source cloud monitoring agent replacement rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext

# Strings that indicate cloud-specific monitoring/management agents
_AGENT_HINTS = [
    "ssm", "amazon-ssm", "aws-ssm",
    "cloudwatch", "amazon-cloudwatch",
    "awsagent",
    "azure monitor", "azure-monitor", "ama", "microsoft monitoring",
    "mma", "oms",
    "stackdriver", "google-cloud-ops",
    "ibm cloudwatch",
]


def _detect_agents(source_vm: VMSpec) -> list[str]:
    """Return list of detected cloud agent hints from os_name and tags."""
    combined = " ".join([
        (source_vm.os_name or ""),
        " ".join(f"{k} {v}" for k, v in source_vm.tags.items()),
        str(source_vm.extra.get("installed_agents", "")),
    ]).lower()

    return [hint for hint in _AGENT_HINTS if hint in combined]


class AGENT001Rule(AssessmentRule):
    """AGENT-001 (info): Cloud-specific management agents need replacement.

    Source-cloud agents (SSM, CloudWatch, Azure Monitor, Stackdriver) are
    provider-specific and must be replaced with the target provider's equivalent
    after migration.
    """

    rule_id = "AGENT-001"
    severity = RuleSeverity.info
    applies_to = "source"
    title = "Source-cloud monitoring/management agents require replacement"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        os_name = (source_vm.os_name or "").lower()
        # Only apply to Linux VMs (Windows agents handled differently)
        if "windows" in os_name:
            return None

        detected = _detect_agents(source_vm)
        if not detected:
            return None

        target_agent = {
            ProviderName.azure: "Azure Monitor Agent (AMA)",
            ProviderName.aws: "Amazon CloudWatch Agent / AWS SSM Agent",
            ProviderName.gcp: "Google Cloud Ops Agent",
            ProviderName.ibm: "IBM Log Analysis / IBM Monitoring agent",
        }.get(target_provider, "target provider monitoring agent")

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM has cloud-specific agent(s) detected: {detected}. "
                f"These will not function on {target_provider.value}."
            ),
            evidence={
                "detected_agents": detected,
                "source_provider": source_vm.provider.value,
                "target_provider": target_provider.value,
            },
            remediation=(
                f"After migration, uninstall source-cloud agents and install {target_agent}. "
                "Update any monitoring dashboards, alerts, and SIEM integrations."
            ),
        )
