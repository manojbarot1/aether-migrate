"""Assessment Pydantic models for AETHER MIGRATE."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from core.models import ProviderName
from pydantic import BaseModel


class RuleSeverity(str, Enum):
    blocker = "blocker"
    warning = "warning"
    info = "info"


class FindingRecord(BaseModel):
    rule_id: str               # e.g. "OS-001"
    rule_version: str          # e.g. "1.0"
    severity: RuleSeverity
    applies_to: str            # "source" or "target"
    title: str
    message: str               # human-readable, safe for display
    evidence: dict[str, Any]   # fields that triggered the rule
    remediation: str           # what to do
    docs_url: str | None = None
    acknowledged: bool = False
    acknowledged_reason: str | None = None


class ReadinessScore(BaseModel):
    """
    Scoring algorithm (deterministic):
      - If any blocker:  status="blocked",              score=0
      - Else:            score = max(0, 100 - (warnings * 10) - (info * 2))
      - If warnings > 0: status="ready_with_warnings"
      - Else:            status="ready",                score=100
    """

    status: Literal["blocked", "ready_with_warnings", "ready"]
    score: int        # 0-100
    blockers: int
    warnings: int
    info: int


class AssessmentResult(BaseModel):
    resource_id: str
    target_provider: ProviderName
    target_region: str
    readiness: ReadinessScore
    findings: list[FindingRecord]
    snapshot_id: str
    snapshot_time: datetime
    catalog_version: str


def compute_readiness_score(findings: list[FindingRecord]) -> ReadinessScore:
    """Compute a ReadinessScore from a list of findings.

    Algorithm (deterministic, documented here and in ReadinessScore docstring):
      - Count blockers, warnings, and info findings (skip acknowledged ones).
      - If any unacknowledged blocker:  status="blocked", score=0.
      - Else score = max(0, 100 - (warnings * 10) - (info * 2)).
      - If unacknowledged warnings > 0: status="ready_with_warnings".
      - Else: status="ready", score=100.
    """
    blockers = sum(
        1 for f in findings
        if f.severity == RuleSeverity.blocker and not f.acknowledged
    )
    warnings = sum(
        1 for f in findings
        if f.severity == RuleSeverity.warning and not f.acknowledged
    )
    info = sum(
        1 for f in findings
        if f.severity == RuleSeverity.info and not f.acknowledged
    )

    if blockers > 0:
        return ReadinessScore(status="blocked", score=0, blockers=blockers, warnings=warnings, info=info)

    score = max(0, 100 - (warnings * 10) - (info * 2))
    if warnings > 0:
        status: Literal["blocked", "ready_with_warnings", "ready"] = "ready_with_warnings"
    else:
        status = "ready"
        score = 100

    return ReadinessScore(status=status, score=score, blockers=0, warnings=warnings, info=info)
