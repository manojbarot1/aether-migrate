"""Assessment engine and CatalogContext for AETHER MIGRATE."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from core.models import ProviderName, ResourceEdge, VMSpec

from assessment.models import AssessmentResult, FindingRecord, compute_readiness_score
from assessment.rule_base import AssessmentRule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


@dataclass
class DiskLimits:
    """Maximum disk size/performance for a given disk type."""

    max_size_gib: int | None
    max_iops: int | None
    max_throughput_mbps: int | None


class CatalogContext:
    """Read-only, pre-loaded view of catalog data for assessment rules.

    Rules must not access the database directly. All required catalog
    information is passed through this context object, which is populated
    from the DB before rules run.

    All methods are synchronous — CatalogContext is built once per
    assessment run and rules call it as pure functions.
    """

    def __init__(
        self,
        arm64_regions: dict[str, set[str]],
        quotas: dict[tuple[str, str, str], int],
        disk_limits: dict[tuple[str, str], DiskLimits],
        catalog_version: str,
        has_only_gen2: bool = False,
    ) -> None:
        # provider_value -> set of region names with arm64 SKUs
        self._arm64_regions = arm64_regions
        # (provider_value, region, vcpu_family) -> quota
        self._quotas = quotas
        # (provider_value, disk_type) -> DiskLimits
        self._disk_limits = disk_limits
        self._catalog_version = catalog_version
        self._has_only_gen2 = has_only_gen2

    @property
    def catalog_version(self) -> str:
        return self._catalog_version

    def has_arm64_in_region(self, provider: ProviderName, region: str) -> bool:
        """Return True if at least one arm64 SKU exists in provider/region."""
        return region in self._arm64_regions.get(provider.value, set())

    def get_quota(
        self,
        provider: ProviderName,
        region: str,
        vcpu_family: str,
    ) -> int | None:
        """Return vCPU quota for the given family, or None if unknown."""
        return self._quotas.get((provider.value, region, vcpu_family))

    def get_disk_limits(
        self,
        provider: ProviderName,
        disk_type: str,
    ) -> DiskLimits | None:
        """Return disk limits for the given provider and disk type, or None."""
        return self._disk_limits.get((provider.value, disk_type))

    def has_only_gen2_in_context(self) -> bool:
        """Return True if all available target SKUs are Gen2-only (UEFI required)."""
        return self._has_only_gen2

    @classmethod
    async def from_db(
        cls,
        target_provider: ProviderName,
        target_region: str,
        db: AsyncSession,
    ) -> CatalogContext:
        """Build a CatalogContext by querying the catalog tables.

        Loads:
        - Which regions have arm64 SKUs per provider
        - Disk limits per provider+disk_type
        - Catalog version (most recent)
        """
        from db.models import CatalogDiskPriceRow, CatalogInstanceTypeRow
        from sqlalchemy import select

        # --- arm64 regions ---
        arm64_result = await db.execute(
            select(
                CatalogInstanceTypeRow.provider,
                CatalogInstanceTypeRow.region,
            ).where(
                CatalogInstanceTypeRow.cpu_arch == "arm64",
                CatalogInstanceTypeRow.available_in_region.is_(True),
                CatalogInstanceTypeRow.restricted.is_(False),
            ).distinct()
        )
        arm64_regions: dict[str, set[str]] = {}
        for row in arm64_result.all():
            arm64_regions.setdefault(row.provider, set()).add(row.region)

        # --- disk limits ---
        disk_limit_result = await db.execute(
            select(CatalogDiskPriceRow).where(
                CatalogDiskPriceRow.provider == target_provider.value,
                CatalogDiskPriceRow.region == target_region,
            )
        )
        disk_limits: dict[tuple[str, str], DiskLimits] = {}
        for row in disk_limit_result.scalars().all():
            disk_limits[(row.provider, row.disk_type)] = DiskLimits(
                max_size_gib=row.max_size_gib,
                max_iops=row.max_iops,
                max_throughput_mbps=row.max_throughput_mbps,
            )

        # --- catalog version ---
        cv_result = await db.execute(
            select(CatalogInstanceTypeRow.catalog_version)
            .where(CatalogInstanceTypeRow.provider == target_provider.value)
            .order_by(CatalogInstanceTypeRow.effective_from.desc())
            .limit(1)
        )
        cv_row = cv_result.scalar_one_or_none()
        catalog_version = cv_row or "unknown"

        return cls(
            arm64_regions=arm64_regions,
            quotas={},  # quotas populated separately if available
            disk_limits=disk_limits,
            catalog_version=catalog_version,
        )


class AssessmentEngine:
    """Runs all registered rules against a source VM and produces an AssessmentResult.

    Rules are pure functions injected at construction time. The engine:
      1. Builds a CatalogContext from the database.
      2. Runs all applicable rules.
      3. Applies finding acknowledgements (marks findings as acknowledged).
      4. Computes a ReadinessScore.
      5. Returns an AssessmentResult.
    """

    def __init__(self, rules: list[AssessmentRule]) -> None:
        self._rules = rules

    async def run(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        edges: list[ResourceEdge],
        db: AsyncSession,
        acknowledgements: list[Any] | None = None,
    ) -> AssessmentResult:
        """Run all rules and return an AssessmentResult.

        Args:
            source_vm: The normalized VM to assess.
            target_provider: The cloud provider to migrate to.
            target_region: The target region.
            edges: Topology edges for the VM (used by DEP-001).
            db: Async DB session for loading catalog context.
            acknowledgements: FindingAcknowledgementRow objects (optional).
        """
        # 1. Build catalog context
        catalog = await CatalogContext.from_db(target_provider, target_region, db)

        # Inject topology edges into DEP-001-style rules
        from assessment.rules.dependency_rules import DEP001Rule

        active_rules: list[AssessmentRule] = []
        for rule in self._rules:
            if isinstance(rule, DEP001Rule):
                # Re-instantiate with current edges
                active_rules.append(DEP001Rule(edges=edges))
            else:
                active_rules.append(rule)

        # 2. Run all rules
        findings: list[FindingRecord] = []
        for rule in active_rules:
            try:
                result = rule.check(source_vm, target_provider, target_region, catalog)
                if result is not None:
                    findings.append(result)
            except Exception:
                log.warning(
                    "assessment_rule_error",
                    rule_id=rule.rule_id,
                    vm_id=str(source_vm.id),
                    exc_info=True,
                )

        # 3. Apply acknowledgements
        if acknowledgements:
            ack_map: dict[str, tuple[str, str]] = {}
            now = datetime.now(UTC)
            for ack in acknowledgements:
                expires = getattr(ack, "expires_at", None)
                if expires is not None:
                    # Make sure we compare tz-aware datetimes
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=UTC)
                    if expires <= now:
                        continue  # expired acknowledgement
                ack_map[ack.rule_id] = (
                    str(ack.acknowledged_by),
                    ack.reason,
                )

            findings = [
                f.model_copy(update={
                    "acknowledged": True,
                    "acknowledged_reason": ack_map[f.rule_id][1],
                }) if f.rule_id in ack_map else f
                for f in findings
            ]

        # 4. Compute readiness score
        readiness = compute_readiness_score(findings)

        # 5. Build result
        snapshot_id = str(source_vm.snapshot_id) if source_vm.snapshot_id else ""
        snapshot_time = source_vm.discovered_at

        return AssessmentResult(
            resource_id=str(source_vm.id),
            target_provider=target_provider,
            target_region=target_region,
            readiness=readiness,
            findings=findings,
            snapshot_id=snapshot_id,
            snapshot_time=snapshot_time,
            catalog_version=catalog.catalog_version,
        )
