"""DEP-001 — dependency topology rule for coordinated migration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import EdgeKind, ProviderName, ResourceEdge, VMSpec

from assessment.models import FindingRecord, RuleSeverity
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


class DEP001Rule(AssessmentRule):
    """DEP-001 (warning): Resource has topology dependencies requiring coordinated migration.

    If the VM is behind a load balancer or shares a disk with other VMs,
    migrating it in isolation will break the dependent resources.
    """

    rule_id = "DEP-001"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "Topology dependencies require coordinated migration"

    def __init__(self, edges: list[ResourceEdge] | None = None) -> None:
        self._edges: list[ResourceEdge] = edges or []

    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        vm_id = source_vm.id

        behind_lb_edges = [
            e for e in self._edges
            if e.from_id == vm_id and e.kind == EdgeKind.behind_lb
        ]
        shared_disk_edges = [
            e for e in self._edges
            if e.from_id == vm_id and e.kind == EdgeKind.attached_to
        ]

        if not behind_lb_edges and not shared_disk_edges:
            return None

        dependency_details: list[dict] = []
        for e in behind_lb_edges:
            dependency_details.append({
                "type": "behind_lb",
                "target_resource_id": str(e.to_id),
            })
        for e in shared_disk_edges:
            dependency_details.append({
                "type": "shared_disk",
                "target_resource_id": str(e.to_id),
            })

        issues = []
        if behind_lb_edges:
            issues.append(f"{len(behind_lb_edges)} load balancer(s)")
        if shared_disk_edges:
            issues.append(f"{len(shared_disk_edges)} shared disk(s)")

        return FindingRecord(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            severity=self.severity,
            applies_to=self.applies_to,
            title=self.title,
            message=(
                f"Source VM has topology dependencies: {', '.join(issues)}. "
                "Migrating this VM in isolation will cause service disruption."
            ),
            evidence={
                "vm_id": str(vm_id),
                "dependencies": dependency_details,
            },
            remediation=(
                "Plan a coordinated migration wave that includes all dependent resources. "
                "For load-balanced VMs: use blue/green migration pattern. "
                "For shared disks: migrate all VMs in the same maintenance window."
            ),
        )
