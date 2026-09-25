"""Tests for AssessmentEngine, readiness scoring, and acknowledgements.

Uses an in-memory SQLite database seeded with minimal catalog data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from assessment.engine import AssessmentEngine, CatalogContext, DiskLimits
from assessment.models import (
    FindingRecord,
    RuleSeverity,
    compute_readiness_score,
)
from assessment.rule_base import AssessmentRule
from core.models import ProviderName, VMSpec
from db.models import Base, CatalogInstanceTypeRow
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("cccc0000-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("dddd0000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    eff = datetime(2025, 1, 1)
    async with _session_factory() as db:
        db.add(CatalogInstanceTypeRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version="2025-01",
            effective_from=eff,
            region="eastus",
            sku="Standard_D4s_v5",
            vcpu=4,
            memory_mib=16384,
            cpu_arch="x86_64",
            local_nvme_gib=0,
            os_support={"linux": True, "windows": True},
            available_in_region=True,
            restricted=False,
        ))
        await db.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def make_vm(**kwargs: Any) -> VMSpec:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": WORKSPACE_ID,
        "connection_id": CONNECTION_ID,
        "provider": ProviderName.aws,
        "native_id": "i-engine-test",
        "account": "123456789",
        "region": "us-east-1",
        "vcpu": 4,
        "memory_gib": 16.0,
        "instance_type": "m5.xlarge",
        "architecture": "x86_64",
        "os_name": "Ubuntu",
        "os_version": "22.04",
        "tags": {},
        "extra": {},
    }
    defaults.update(kwargs)
    return VMSpec(**defaults)


# ---------------------------------------------------------------------------
# Readiness score formula
# ---------------------------------------------------------------------------


class TestReadinessScoreFormula:
    def test_blocked_when_any_blocker(self) -> None:
        findings = [
            FindingRecord(
                rule_id="OS-001", rule_version="1.0", severity=RuleSeverity.blocker,
                applies_to="source", title="T", message="M", evidence={}, remediation="R",
            )
        ]
        score = compute_readiness_score(findings)
        assert score.status == "blocked"
        assert score.score == 0
        assert score.blockers == 1

    def test_ready_with_warnings_when_no_blockers_but_warnings(self) -> None:
        findings = [
            FindingRecord(
                rule_id=f"W-{i}", rule_version="1.0", severity=RuleSeverity.warning,
                applies_to="source", title="T", message="M", evidence={}, remediation="R",
            )
            for i in range(3)
        ]
        score = compute_readiness_score(findings)
        assert score.status == "ready_with_warnings"
        assert score.score == max(0, 100 - 3 * 10)  # = 70
        assert score.warnings == 3

    def test_ready_when_no_findings(self) -> None:
        score = compute_readiness_score([])
        assert score.status == "ready"
        assert score.score == 100
        assert score.blockers == 0
        assert score.warnings == 0

    def test_score_minimum_is_zero(self) -> None:
        # 11 warnings = 100 - 110 = -10 → clamped to 0
        findings = [
            FindingRecord(
                rule_id=f"W-{i}", rule_version="1.0", severity=RuleSeverity.warning,
                applies_to="source", title="T", message="M", evidence={}, remediation="R",
            )
            for i in range(11)
        ]
        score = compute_readiness_score(findings)
        assert score.score == 0
        assert score.status == "ready_with_warnings"

    def test_acknowledged_blockers_do_not_count(self) -> None:
        findings = [
            FindingRecord(
                rule_id="OS-001", rule_version="1.0", severity=RuleSeverity.blocker,
                applies_to="source", title="T", message="M", evidence={}, remediation="R",
                acknowledged=True, acknowledged_reason="Accepted risk",
            )
        ]
        score = compute_readiness_score(findings)
        # Acknowledged blockers are excluded → no unacknowledged blockers → ready
        assert score.status == "ready"
        assert score.score == 100

    def test_info_only_does_not_affect_status_when_no_warnings(self) -> None:
        # Per the algorithm: info items only deduct from score when warnings > 0.
        # If warnings == 0 → status="ready", score=100 (info penalty only applies
        # within the ready_with_warnings path which requires at least one warning).
        findings = [
            FindingRecord(
                rule_id=f"INFO-{i}", rule_version="1.0", severity=RuleSeverity.info,
                applies_to="source", title="T", message="M", evidence={}, remediation="R",
            )
            for i in range(5)
        ]
        score = compute_readiness_score(findings)
        # No warnings → status="ready", score=100 even with info findings
        assert score.status == "ready"
        assert score.score == 100
        assert score.info == 5


# ---------------------------------------------------------------------------
# AssessmentEngine integration
# ---------------------------------------------------------------------------


class _AlwaysBlockerRule(AssessmentRule):
    rule_id = "TEST-BLOCKER"
    rule_version = "1.0"
    severity = RuleSeverity.blocker
    applies_to = "source"
    title = "Always-blocker test rule"

    def check(self, source_vm, target_provider, target_region, catalog):
        return FindingRecord(
            rule_id=self.rule_id, rule_version=self.rule_version,
            severity=self.severity, applies_to=self.applies_to,
            title=self.title, message="Test blocker", evidence={"test": True},
            remediation="This is a test",
        )


class _AlwaysPassRule(AssessmentRule):
    rule_id = "TEST-PASS"
    rule_version = "1.0"
    severity = RuleSeverity.warning
    applies_to = "source"
    title = "Always-pass test rule"

    def check(self, source_vm, target_provider, target_region, catalog):
        return None


class TestAssessmentEngineRun:
    async def test_engine_returns_assessment_result(self) -> None:
        engine = AssessmentEngine(rules=[_AlwaysPassRule()])
        vm = make_vm()

        async with _session_factory() as db:
            result = await engine.run(
                source_vm=vm,
                target_provider=ProviderName.azure,
                target_region="eastus",
                edges=[],
                db=db,
            )

        assert result.resource_id == str(vm.id)
        assert result.target_provider == ProviderName.azure
        assert result.target_region == "eastus"
        assert result.readiness.status == "ready"
        assert result.findings == []

    async def test_engine_collects_all_rule_findings(self) -> None:
        engine = AssessmentEngine(rules=[_AlwaysBlockerRule(), _AlwaysPassRule()])
        vm = make_vm()

        async with _session_factory() as db:
            result = await engine.run(
                source_vm=vm,
                target_provider=ProviderName.azure,
                target_region="eastus",
                edges=[],
                db=db,
            )

        assert len(result.findings) == 1
        assert result.findings[0].rule_id == "TEST-BLOCKER"
        assert result.readiness.status == "blocked"
        assert result.readiness.score == 0

    async def test_acknowledgement_marks_finding_acknowledged(self) -> None:
        engine = AssessmentEngine(rules=[_AlwaysBlockerRule()])
        vm = make_vm()

        # Mock an acknowledgement
        class MockAck:
            rule_id = "TEST-BLOCKER"
            acknowledged_by = uuid.uuid4()
            reason = "Known acceptable risk"
            expires_at = None

        async with _session_factory() as db:
            result = await engine.run(
                source_vm=vm,
                target_provider=ProviderName.azure,
                target_region="eastus",
                edges=[],
                db=db,
                acknowledgements=[MockAck()],
            )

        assert len(result.findings) == 1
        assert result.findings[0].acknowledged is True
        assert result.findings[0].acknowledged_reason == "Known acceptable risk"
        # Acknowledged blocker → not blocked
        assert result.readiness.status == "ready"

    async def test_expired_acknowledgement_is_not_applied(self) -> None:
        engine = AssessmentEngine(rules=[_AlwaysBlockerRule()])
        vm = make_vm()

        class MockExpiredAck:
            rule_id = "TEST-BLOCKER"
            acknowledged_by = uuid.uuid4()
            reason = "Expired"
            expires_at = datetime(2020, 1, 1, tzinfo=UTC)  # in the past

        async with _session_factory() as db:
            result = await engine.run(
                source_vm=vm,
                target_provider=ProviderName.azure,
                target_region="eastus",
                edges=[],
                db=db,
                acknowledgements=[MockExpiredAck()],
            )

        # Expired ack should NOT apply
        assert result.findings[0].acknowledged is False
        assert result.readiness.status == "blocked"

    async def test_readiness_blocked_when_blocker_rule_fires(self) -> None:
        engine = AssessmentEngine(rules=[_AlwaysBlockerRule()])
        vm = make_vm()

        async with _session_factory() as db:
            result = await engine.run(
                source_vm=vm,
                target_provider=ProviderName.azure,
                target_region="eastus",
                edges=[],
                db=db,
            )

        assert result.readiness.status == "blocked"
        assert result.readiness.score == 0
        assert result.readiness.blockers == 1


# ---------------------------------------------------------------------------
# CatalogContext
# ---------------------------------------------------------------------------


class TestCatalogContext:
    def test_has_arm64_in_region_returns_true_when_present(self) -> None:
        ctx = CatalogContext(
            arm64_regions={"azure": {"eastus", "westus2"}},
            quotas={},
            disk_limits={},
            catalog_version="2025-01",
        )
        assert ctx.has_arm64_in_region(ProviderName.azure, "eastus") is True
        assert ctx.has_arm64_in_region(ProviderName.azure, "southcentralus") is False

    def test_get_quota_returns_none_when_not_set(self) -> None:
        ctx = CatalogContext(
            arm64_regions={},
            quotas={("azure", "eastus", "standardDSv5Family"): 100},
            disk_limits={},
            catalog_version="2025-01",
        )
        assert ctx.get_quota(ProviderName.azure, "eastus", "standardDSv5Family") == 100
        assert ctx.get_quota(ProviderName.azure, "westus", "standardDSv5Family") is None

    def test_get_disk_limits_returns_correct_limits(self) -> None:
        limits = DiskLimits(max_size_gib=32767, max_iops=20000, max_throughput_mbps=900)
        ctx = CatalogContext(
            arm64_regions={},
            quotas={},
            disk_limits={("azure", "premium_lrs"): limits},
            catalog_version="2025-01",
        )
        result = ctx.get_disk_limits(ProviderName.azure, "premium_lrs")
        assert result is not None
        assert result.max_size_gib == 32767
        assert ctx.get_disk_limits(ProviderName.azure, "unknown") is None
