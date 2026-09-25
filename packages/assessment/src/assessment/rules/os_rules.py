"""OS-001 and OS-002 — operating system compatibility and end-of-life rules."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.data.os_eol import lookup_eol
from assessment.data.os_support import SUPPORTED_OS
from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


def _is_windows(os_name: str) -> bool:
    return "windows" in os_name.lower()


class OS001Rule(AssessmentRule):
    """OS-001 (blocker): Target provider does not support the source OS.

    Uses an allowlist in data/os_support.py. If the source OS family+version
    is not in the allowlist for the target provider, block migration.
    """

    rule_id = "OS-001"
    severity = RuleSeverity.blocker
    applies_to = "source"
    title = "Unsupported operating system on target provider"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        os_name = (source_vm.os_name or "").strip()
        os_version = (source_vm.os_version or "").strip()

        if not os_name:
            return None  # No OS info — skip; other rules may flag this

        supported = SUPPORTED_OS.get(target_provider.value, {})
        os_name_lower = os_name.lower()
        os_version_lower = os_version.lower()

        if _is_windows(os_name):
            allowed_versions = supported.get("windows", [])
            # Match if any allowed version substring appears in the version string
            if any(v in os_version_lower for v in allowed_versions):
                return None
            return FindingRecord(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                severity=self.severity,
                applies_to=self.applies_to,
                title=self.title,
                message=(
                    f"Windows Server {os_version!r} is not in the supported OS list for "
                    f"{target_provider.value}. Supported Windows versions: "
                    + ", ".join(allowed_versions or ["none"])
                    + "."
                ),
                evidence={"os_name": os_name, "os_version": os_version,
                           "target_provider": target_provider.value},
                remediation=(
                    "Upgrade the OS to a supported Windows Server version before migration, "
                    "or choose a different target provider."
                ),
            )
        else:
            allowed_families = supported.get("linux", [])
            if any(fam in os_name_lower for fam in allowed_families):
                return None
            return FindingRecord(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                severity=self.severity,
                applies_to=self.applies_to,
                title=self.title,
                message=(
                    f"Linux distribution {os_name!r} is not in the supported OS list for "
                    f"{target_provider.value}."
                ),
                evidence={"os_name": os_name, "os_version": os_version,
                           "target_provider": target_provider.value},
                remediation=(
                    "Migrate to a supported Linux distribution, or contact the target "
                    "provider to check custom image support."
                ),
            )


class OS002Rule(AssessmentRule):
    """OS-002 (warning): Source OS is past its end-of-life date.

    Checks the os_eol.py lookup table; if the OS has passed EOL, flags
    as a warning. Missing EOL data is not flagged.
    """

    rule_id = "OS-002"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "Operating system is end-of-life"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        os_name = (source_vm.os_name or "").strip()
        os_version = (source_vm.os_version or "").strip()

        if not os_name:
            return None

        eol_date = lookup_eol(os_name, os_version)
        if eol_date is None:
            return None  # Unknown EOL — don't flag

        today = date.today()
        if today <= eol_date:
            return None  # Still supported

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"{os_name} {os_version} reached end-of-life on {eol_date.isoformat()}. "
                "Running EOL software in the cloud is a security risk."
            ),
            evidence={"os_name": os_name, "os_version": os_version,
                       "eol_date": eol_date.isoformat()},
            remediation=(
                "Upgrade the OS to a supported version before or after migration. "
                "Extended Security Updates (ESU) may be available depending on the provider."
            ),
            docs_url="https://endoflife.date/",
        )
