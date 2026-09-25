"""NET-001, NET-002, NET-003 — network migration rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext

_STATIC_IP_TAG_HINTS = frozenset(["static", "ip", "fixed-ip", "elastic"])


class NET001Rule(AssessmentRule):
    """NET-001 (warning): Private IP addresses will change after migration.

    Cloud private IPs are CIDR-scoped to the VPC/VNet. Any hardcoded
    references to the source private IP will break after migration.
    """

    rule_id = "NET-001"
    severity = RuleSeverity.warning
    applies_to = "target"
    title = "Private IP addresses will change after migration"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        private_ips: list[str] = []
        for nic in source_vm.nics:
            private_ips.extend(nic.private_ips)
        if source_vm.primary_private_ip:
            if source_vm.primary_private_ip not in private_ips:
                private_ips.append(source_vm.primary_private_ip)

        if not private_ips:
            return None

        # Heuristic: check if any tag hints at a static IP assignment
        tag_keys_lower = {k.lower() for k in source_vm.tags}
        tag_vals_lower = {str(v).lower() for v in source_vm.tags.values()}
        has_static_hint = bool(
            tag_keys_lower & _STATIC_IP_TAG_HINTS
            or tag_vals_lower & _STATIC_IP_TAG_HINTS
        )

        message = (
            f"Source VM has {len(private_ips)} private IP address(es) that will be "
            "reassigned in the target VPC/VNet. Any hardcoded IP references will break."
        )
        if has_static_hint:
            message += " Tags suggest a static IP may be in use — verify carefully."

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=message,
            evidence={"private_ip_count": len(private_ips), "has_static_ip_hint": has_static_hint},
            remediation=(
                "Review all application configuration for hardcoded IP addresses. "
                "Use DNS names or service discovery instead. Pre-assign the target IP "
                "from the target subnet's CIDR if the application requires a fixed IP."
            ),
        )


class NET002Rule(AssessmentRule):
    """NET-002 (warning): Security group peer references cannot be 1:1 translated.

    AWS security groups support peer-group (SG-to-SG) rules. Azure NSGs use
    ASGs (Application Security Groups) which have different semantics.
    GCP uses network tags which are similar but not identical.
    """

    rule_id = "NET-002"
    severity = RuleSeverity.warning
    applies_to = "target"
    title = "Security group peer references require manual translation"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        # peer_group rules are stored in SecurityRule.peer_group on the SG resources,
        # but VMSpec carries security_group_ids. We use extra["security_group_peer_rules"]
        # which the normalizer may set, or fall back to checking tags.
        sg_peer_count: int = source_vm.extra.get("security_group_peer_rule_count", 0)

        if target_provider == ProviderName.aws:
            return None  # Same model — no translation needed

        if sg_peer_count == 0:
            return None

        target_model = {
            ProviderName.azure: "Azure Application Security Groups (ASGs)",
            ProviderName.gcp: "GCP network tags",
            ProviderName.ibm: "IBM Security Groups",
        }.get(target_provider, "equivalent construct")

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM's security groups contain {sg_peer_count} peer-group rule(s) "
                f"(SG-to-SG). These cannot be 1:1 translated to {target_model}."
            ),
            evidence={
                "security_group_peer_rule_count": sg_peer_count,
                "target_provider": target_provider.value,
                "target_construct": target_model,
            },
            remediation=(
                f"Manually re-create peer rules using {target_model}. "
                "Review each SG rule and determine the equivalent construct on the target."
            ),
        )


class NET003Rule(AssessmentRule):
    """NET-003 (info): Public IP or DNS cutover required.

    If the source VM has public IPs or a DNS name in tags, flag that
    public-facing cutover planning is needed.
    """

    rule_id = "NET-003"
    severity = RuleSeverity.info
    applies_to = "target"
    title = "Public IP or DNS cutover required"

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        public_ips: list[str] = []
        for nic in source_vm.nics:
            public_ips.extend(nic.public_ips)
        if source_vm.primary_public_ip:
            if source_vm.primary_public_ip not in public_ips:
                public_ips.append(source_vm.primary_public_ip)

        # Check tags for DNS name hints
        tags_combined = " ".join(
            f"{k} {v}" for k, v in source_vm.tags.items()
        ).lower()
        has_dns_hint = "dns" in tags_combined or "fqdn" in tags_combined or "hostname" in tags_combined

        if not public_ips and not has_dns_hint:
            return None

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM has {len(public_ips)} public IP(s)"
                + (" and DNS name hints in tags" if has_dns_hint else "")
                + ". Plan for public IP/DNS cutover to minimize downtime."
            ),
            evidence={
                "public_ip_count": len(public_ips),
                "has_dns_hint": has_dns_hint,
            },
            remediation=(
                "Allocate a public IP on the target, update DNS records using low TTL "
                "before cutover, and plan the switchover window. "
                "Consider using Azure Traffic Manager or AWS Route53 weighted routing."
            ),
        )
