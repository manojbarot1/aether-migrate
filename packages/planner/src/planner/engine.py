"""AETHER MIGRATE — plan engine (Phase 7).

Deterministic plan generation from sizing + assessment results.
No LLM calls — pure arithmetic and rule application.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from core.models import ProviderName, VMSpec
from pydantic import BaseModel
from sizing.engine import SizingEngine, SizingStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from planner.models import MigrationPlan, PlanStep, ResourceSummary

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public options model
# ---------------------------------------------------------------------------


class PlanOptions(BaseModel):
    target_provider: ProviderName
    target_region: str
    sizing_strategy: SizingStrategy = SizingStrategy.right_sized
    include_disk_migration: bool = True
    dual_run_days: int = 14
    assumed_bandwidth_mbps: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _total_disk_gib(vm: VMSpec) -> float:
    return sum(d.size_gib or 0.0 for d in vm.disks)


def _compute_downtime(
    vms: list[VMSpec],
    options: PlanOptions,
) -> tuple[int, str]:
    """Return (downtime_minutes, basis_string)."""
    total_gib = sum(_total_disk_gib(vm) for vm in vms)

    bandwidth_mbps: float | None = options.assumed_bandwidth_mbps
    if bandwidth_mbps is None:
        # Try to find from metrics
        for vm in vms:
            if vm.metrics and vm.metrics.net_mbps_p95:
                bandwidth_mbps = vm.metrics.net_mbps_p95
                break

    if bandwidth_mbps and bandwidth_mbps > 0:
        transfer_minutes = (total_gib * 1024) / bandwidth_mbps / 60
        downtime = int(transfer_minutes + 10)  # +10 min cutover window
        basis = (
            f"data transfer: {total_gib:.0f} GiB at {bandwidth_mbps:.0f} Mbps "
            f"≈ {transfer_minutes:.0f} min + 10 min cutover window"
        )
    else:
        downtime = 0
        basis = "unknown — provide bandwidth estimate via assumed_bandwidth_mbps"

    return downtime, basis


def _generate_prerequisites(
    vms: list[VMSpec],
    options: PlanOptions,
    findings: list[Any],
    vcpu_total: int,
) -> list[str]:
    """Generate plain-English prerequisites list."""
    prereqs: list[str] = []

    prereqs.append(
        f"Create target landing zone (VNet/VPC, subnets, security groups) using "
        f"the generated OpenTofu module for {options.target_provider.value}/{options.target_region}"
    )
    if vcpu_total > 0:
        prereqs.append(
            f"Verify target region vCPU quota: need {vcpu_total} vCPU "
            f"in {options.target_provider.value}/{options.target_region}"
        )

    # Check for DRV-001 (guest driver finding)
    driver_vms = [
        f.evidence.get("vm_id", "VM")
        for f in findings
        if f.rule_id == "DRV-001" and not f.acknowledged
    ]
    if driver_vms:
        prereqs.append(
            "Install guest drivers (e.g. VirtIO/Azure Linux Agent) on source VM(s) "
            "before the migration window (required by DRV-001 finding)"
        )

    # Blocker findings that are NOT acknowledged become hard prerequisites
    blockers = [f for f in findings if f.severity.value == "blocker" and not f.acknowledged]
    for b in blockers:
        prereqs.append(f"[MUST RESOLVE] {b.title}: {b.remediation}")

    return prereqs


def _generate_steps(
    vms: list[VMSpec],
    options: PlanOptions,
    findings: list[Any],
) -> list[PlanStep]:
    """Generate standard AWS→Azure migration steps."""
    has_mgn_available = True  # optimistic; user can override in practice

    steps: list[PlanStep] = [
        PlanStep(
            step_number=1,
            title="Pre-flight check",
            description="Verify all prerequisites are in place before starting the migration.",
            pre_check="Confirm operator access to source and target environments.",
            action=(
                "Check IAM/RBAC permissions, vCPU quota, network connectivity between "
                "source and target, and that all blocker findings are resolved."
            ),
            post_check="All permission and quota checks pass with no errors.",
            compensation="Do not proceed — resolve all pre-flight failures first.",
            estimated_duration_minutes=30,
            is_manual=True,
        ),
        PlanStep(
            step_number=2,
            title="Create target network resources",
            description=(
                "Provision VNet, subnets, and NSGs in the target region using the "
                "generated OpenTofu module."
            ),
            pre_check="OpenTofu module reviewed and variables.tf populated.",
            action=(
                "Run `tofu init && tofu plan && tofu apply` for the generated "
                f"module targeting {options.target_provider.value}/{options.target_region}."
            ),
            post_check="Azure resource group, VNet, subnet, and NSG exist and are healthy.",
            compensation="Run `tofu destroy` to remove created resources.",
            estimated_duration_minutes=15,
            is_manual=False,
        ),
        PlanStep(
            step_number=3,
            title="Snapshot source VM disk(s)",
            description="Create a consistent point-in-time snapshot of all source VM disks.",
            pre_check="Source VM is quiesced or in a known-good state.",
            action=(
                "Create EBS snapshots for all attached volumes on the source VM. "
                "Record snapshot IDs for transfer."
            ),
            post_check="All EBS snapshots are in 'completed' state.",
            compensation="Delete created snapshots.",
            estimated_duration_minutes=20,
            is_manual=False,
        ),
    ]

    if has_mgn_available:
        steps.append(PlanStep(
            step_number=4,
            title="Configure AWS MGN for source VM",
            description="Set up AWS Application Migration Service (MGN) replication.",
            pre_check=(
                "AWS MGN is enabled in the source region. "
                "Replication agent can reach MGN endpoint."
            ),
            action=(
                "Install the AWS Replication Agent on the source VM. "
                "Configure MGN launch settings for target instance type and network."
            ),
            post_check="Source VM appears as 'Healthy' in AWS MGN console.",
            compensation="Remove source server from MGN; uninstall replication agent.",
            estimated_duration_minutes=45,
            is_manual=True,
        ))
    else:
        steps.append(PlanStep(
            step_number=4,
            title="Export snapshot to S3",
            description="Export EBS snapshots to S3 as VMDKs for transfer.",
            pre_check="S3 bucket created in source region with cross-region replication enabled.",
            action="Use `ec2 export-snapshot` to export snapshots to S3 in VMDK format.",
            post_check="VMDK files present in S3 with correct size.",
            compensation="Delete VMDK files from S3.",
            estimated_duration_minutes=120,
            is_manual=False,
        ))

    steps += [
        PlanStep(
            step_number=5,
            title="Transfer / replicate data",
            description="Replicate data from source to target until delta is minimal.",
            pre_check="Step 4 completed. Replication lag is below threshold.",
            action=(
                "Monitor MGN replication progress. Wait until lag is < 10 GiB "
                "before scheduling the cutover window."
            ),
            post_check="MGN shows 'Ready for cutover' status.",
            compensation="Abort cutover; continue replication from last checkpoint.",
            estimated_duration_minutes=180,
            is_manual=False,
        ),
        PlanStep(
            step_number=6,
            title="Test boot in isolated network",
            description="Boot a test instance from the replicated data in an isolated VNet.",
            pre_check="Test VNet with no external routing created in target region.",
            action=(
                "Launch test instance via MGN test cutover. "
                "Verify OS boots and services start."
            ),
            post_check="Test instance is accessible on its private IP and services respond.",
            compensation="Terminate test instance; clean up test resources.",
            estimated_duration_minutes=60,
            is_manual=True,
        ),
        PlanStep(
            step_number=7,
            title="Application validation",
            description="Run application smoke tests against the test instance.",
            pre_check="Test instance is booted and network connectivity confirmed.",
            action=(
                "Execute application test suite against test instance. "
                "Verify database connectivity, API responses, and key workflows."
            ),
            post_check="All smoke tests pass. No critical application errors in logs.",
            compensation="Terminate test instance; document failures for remediation.",
            estimated_duration_minutes=120,
            is_manual=True,
        ),
        PlanStep(
            step_number=8,
            title="Cutover approval",
            description=(
                "Obtain written approval from designated approver before proceeding "
                "with production cutover."
            ),
            pre_check="All previous steps completed successfully with no open issues.",
            action=(
                "Submit cutover request in AETHER MIGRATE. "
                "Designated approver reviews plan hash and approves."
            ),
            post_check="Approval record created with valid, non-expired hash.",
            compensation="Cancel cutover request; do not proceed.",
            estimated_duration_minutes=0,
            is_manual=True,
        ),
        PlanStep(
            step_number=9,
            title="Final sync",
            description="Perform final data synchronization immediately before cutover.",
            pre_check="Cutover window is active. Source application traffic is quiesced.",
            action=(
                "Trigger MGN final sync. "
                "Wait for replication lag to reach zero."
            ),
            post_check="MGN reports 'Cut over' or zero lag.",
            compensation="Resume source VM; restore application traffic.",
            estimated_duration_minutes=30,
            is_manual=False,
        ),
        PlanStep(
            step_number=10,
            title="DNS / LB cutover",
            description="Switch DNS records and load balancer targets to point to the new VM.",
            pre_check="New VM is running and passing health checks.",
            action=(
                "Update DNS records and/or load balancer target groups to point to "
                "the new Azure VM's private/public IP."
            ),
            post_check="DNS resolves to new IP. LB health checks pass.",
            compensation="Revert DNS records and LB targets to source VM.",
            estimated_duration_minutes=15,
            is_manual=False,
        ),
        PlanStep(
            step_number=11,
            title="Post-migration validation",
            description="Verify production traffic flows correctly through the migrated VM.",
            pre_check="DNS cutover completed.",
            action=(
                "Monitor application metrics, error rates, and latency for "
                f"{options.dual_run_days} days. "
                "Check logs for errors related to migration."
            ),
            post_check=(
                "Error rate ≤ pre-migration baseline. "
                "No migration-related errors in application logs."
            ),
            compensation="Revert DNS/LB to source VM; open incident.",
            estimated_duration_minutes=options.dual_run_days * 24 * 60,
            is_manual=False,
        ),
        PlanStep(
            step_number=12,
            title=f"Hypercare period ({options.dual_run_days} days)",
            description=(
                f"Maintain the source VM in standby for {options.dual_run_days} days "
                "in case rollback is needed."
            ),
            pre_check="Post-migration validation passed.",
            action=(
                "Keep source VM stopped (not terminated). "
                "Monitor for escalations. "
                "After hypercare period, schedule source decommission."
            ),
            post_check="No rollback events. Source VM still accessible if needed.",
            compensation="Start source VM and revert DNS/LB.",
            estimated_duration_minutes=options.dual_run_days * 24 * 60,
            is_manual=True,
        ),
        PlanStep(
            step_number=13,
            title="Source decommission",
            description=(
                "Terminate and clean up the source VM and all associated resources "
                "after the hypercare period."
            ),
            pre_check=(
                f"Hypercare period ({options.dual_run_days} days) completed. "
                "No rollback events."
            ),
            action=(
                "Stop and terminate the source VM. "
                "Delete associated EBS volumes, security groups, "
                "and remove from MGN. "
                "Archive final cost report."
            ),
            post_check="Source resources are terminated. No unexpected costs appearing.",
            compensation="Not applicable — decommission is irreversible; use backups.",
            estimated_duration_minutes=30,
            is_manual=True,
        ),
    ]

    return steps


def _compute_plan_hash(plan: MigrationPlan) -> str:
    """SHA-256 of the plan document serialized to JSON (excluding content_hash field)."""
    doc = plan.model_dump(mode="json", exclude={"content_hash"})
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Plan Engine
# ---------------------------------------------------------------------------


class PlanEngine:
    """Deterministic migration plan generation engine."""

    def __init__(self) -> None:
        self._sizing = SizingEngine()

    async def create(
        self,
        resource_ids: list[uuid.UUID],
        options: PlanOptions,
        snapshot_id: uuid.UUID,
        db: AsyncSession,
        plan_name: str | None = None,
        workspace_id: uuid.UUID | None = None,
        plan_id: uuid.UUID | None = None,
        version: int = 1,
    ) -> MigrationPlan:
        """Create a deterministic MigrationPlan for the given resource IDs.

        Steps:
        1. Load VMSpec records from DB
        2. Run sizing for each VM
        3. Run assessment for each VM
        4. Generate prerequisites, steps, downtime estimate
        5. Compute content_hash
        6. Return MigrationPlan
        """
        from db.models import ResourceRow

        # ------------------------------------------------------------------
        # 1. Load VM records
        # ------------------------------------------------------------------
        from sqlalchemy import select

        q = select(ResourceRow).where(
            ResourceRow.id.in_(resource_ids),
            ResourceRow.kind == "vm",
        )
        result = await db.execute(q)
        rows = result.scalars().all()

        if not rows:
            raise ValueError("No VM resources found for the given resource_ids")

        vms: list[VMSpec] = []
        for row in rows:
            spec = row.spec or {}
            from core.models import DiskSpec, NicSpec

            vm = VMSpec(
                id=row.id,
                workspace_id=row.workspace_id,
                connection_id=row.connection_id,
                provider=ProviderName(row.provider),
                native_id=row.native_id,
                account=row.account,
                region=row.region,
                zone=row.zone,
                name=row.name,
                snapshot_id=row.snapshot_id,
                tags=row.tags or {},
                os_name=spec.get("os_name"),
                os_version=spec.get("os_version"),
                vcpu=spec.get("vcpu"),
                memory_gib=spec.get("memory_gib"),
                architecture=spec.get("architecture"),
                instance_type=spec.get("instance_type"),
                extra=spec.get("extra", {}),
            )
            for d in spec.get("disks", []):
                vm.disks.append(DiskSpec(
                    size_gib=d.get("size_gib"),
                    type_class=d.get("type_class"),
                    iops=d.get("iops"),
                    throughput_mbps=d.get("throughput_mbps"),
                    boot=d.get("boot", False),
                    encrypted=d.get("encrypted", False),
                    ephemeral=d.get("ephemeral", False),
                ))
            for n in spec.get("nics", []):
                vm.nics.append(NicSpec(
                    private_ips=n.get("private_ips", []),
                    public_ips=n.get("public_ips", []),
                    security_group_ids=n.get("security_group_ids", []),
                ))
            vms.append(vm)

        # ------------------------------------------------------------------
        # 2. Run sizing for each VM
        # ------------------------------------------------------------------
        all_sizing_candidates: list[Any] = []
        catalog_version = "unknown"
        catalog_date: datetime = datetime.now(UTC)

        for vm in vms:
            try:
                sizing_results = await self._sizing.recommend(
                    source_vm=vm,
                    target_provider=options.target_provider,
                    target_region=options.target_region,
                    strategies=[options.sizing_strategy],
                    db=db,
                )
                if sizing_results:
                    sr = sizing_results[0]
                    all_sizing_candidates.extend(sr.candidates)
                    catalog_version = sr.catalog_version
                    catalog_date = sr.catalog_date
            except Exception:
                log.warning(
                    "plan_engine.sizing_failed",
                    vm_id=str(vm.id),
                    exc_info=True,
                )

        # ------------------------------------------------------------------
        # 3. Run assessment for each VM
        # ------------------------------------------------------------------
        from assessment.engine import AssessmentEngine
        from assessment.registry import RuleRegistry

        all_findings: list[Any] = []
        ack_rule_ids: list[str] = []

        registry = RuleRegistry.build()
        assessment_engine = AssessmentEngine(rules=registry.get_all())

        for vm in vms:
            try:
                ar = await assessment_engine.run(
                    source_vm=vm,
                    target_provider=options.target_provider,
                    target_region=options.target_region,
                    edges=[],
                    db=db,
                )
                all_findings.extend(ar.findings)
                ack_rule_ids.extend(
                    f.rule_id for f in ar.findings if f.acknowledged
                )
            except Exception:
                log.warning(
                    "plan_engine.assessment_failed",
                    vm_id=str(vm.id),
                    exc_info=True,
                )

        # ------------------------------------------------------------------
        # 4. Generate prerequisites, steps, downtime estimate
        # ------------------------------------------------------------------
        vcpu_total = sum(
            c.vcpu for c in all_sizing_candidates[:len(vms)]
        ) or sum(vm.vcpu or 0 for vm in vms)

        prerequisites = _generate_prerequisites(vms, options, all_findings, vcpu_total)
        steps = _generate_steps(vms, options, all_findings)
        downtime_minutes, downtime_basis = _compute_downtime(vms, options)

        source_resources = [
            ResourceSummary(
                id=str(vm.id),
                name=vm.name,
                kind="vm",
                provider=vm.provider.value,
                region=vm.region,
            )
            for vm in vms
        ]

        assumptions = [
            "Source VMs have stable workloads during the migration window.",
            "Target region has sufficient capacity for the requested instance types.",
            "Network connectivity between source and target is established.",
            "Operator has appropriate IAM/RBAC permissions on both source and target.",
            "Application supports eventual consistency during the dual-run period.",
        ]

        rollback_plan = (
            "In case of critical failure at any step: (1) revert DNS/LB to source VM, "
            "(2) verify source VM is running and accepting traffic, "
            "(3) open incident with migration team, "
            "(4) do not decommission source until root cause is resolved."
        )

        now = datetime.now(UTC)
        effective_plan_id = str(plan_id or uuid.uuid4())
        effective_workspace_id = str(workspace_id or (vms[0].workspace_id if vms else uuid.uuid4()))

        # ------------------------------------------------------------------
        # 5. Build plan and compute hash
        # ------------------------------------------------------------------
        plan = MigrationPlan(
            plan_id=effective_plan_id,
            workspace_id=effective_workspace_id,
            name=plan_name or f"Migration plan — {options.target_provider.value}/{options.target_region}",
            version=version,
            source_resources=source_resources,
            target_provider=options.target_provider,
            target_region=options.target_region,
            sizing_choices=[c.model_dump(mode="json") for c in all_sizing_candidates],
            assessment_findings=[f.model_dump(mode="json") for f in all_findings],
            acknowledged_findings=list(set(ack_rule_ids)),
            prerequisites=prerequisites,
            steps=steps,
            downtime_estimate_minutes=downtime_minutes,
            downtime_basis=downtime_basis,
            rollback_plan=rollback_plan,
            assumptions=assumptions,
            snapshot_id=str(snapshot_id),
            snapshot_time=now,
            catalog_version=catalog_version,
            catalog_date=catalog_date,
            created_at=now,
            content_hash=None,  # computed below
        )

        plan.content_hash = _compute_plan_hash(plan)

        log.info(
            "plan_engine.created",
            plan_id=effective_plan_id,
            vms=len(vms),
            steps=len(steps),
            hash=plan.content_hash[:8],
        )

        return plan
