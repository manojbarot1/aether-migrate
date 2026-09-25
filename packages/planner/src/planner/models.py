"""Planner domain models for AETHER MIGRATE."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models import ProviderName
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Resource summary (lightweight reference used in plan documents)
# ---------------------------------------------------------------------------


class ResourceSummary(BaseModel):
    id: str
    name: str | None
    kind: str         # e.g. "vm"
    provider: str
    region: str


# ---------------------------------------------------------------------------
# Plan step
# ---------------------------------------------------------------------------


class PlanStep(BaseModel):
    step_number: int
    title: str
    description: str
    pre_check: str              # what to verify before this step
    action: str                 # what to do
    post_check: str             # how to verify success
    compensation: str           # how to undo if this step fails
    estimated_duration_minutes: int
    is_manual: bool             # True if a human must perform it


# ---------------------------------------------------------------------------
# Migration plan (immutable document)
# ---------------------------------------------------------------------------


class MigrationPlan(BaseModel):
    """The complete, immutable plan document.

    content_hash is populated by PlanEngine after construction — it is
    excluded from the hash computation itself.
    """

    plan_id: str
    workspace_id: str
    name: str
    version: int

    # Scope
    source_resources: list[ResourceSummary]
    target_provider: ProviderName
    target_region: str
    sizing_choices: list[Any]       # list[SizingCandidate] from Phase 5
    # Findings
    assessment_findings: list[Any]  # list[FindingRecord] from Phase 6
    acknowledged_findings: list[str]  # rule_ids that are acknowledged

    # Prerequisites
    prerequisites: list[str]

    # Steps
    steps: list[PlanStep]

    # Estimates
    downtime_estimate_minutes: int
    downtime_basis: str

    # Rollback
    rollback_plan: str

    # Assumptions
    assumptions: list[str]

    # Metadata
    snapshot_id: str
    snapshot_time: datetime
    catalog_version: str
    catalog_date: datetime
    created_at: datetime

    # Content hash — populated by PlanEngine after construction
    content_hash: str | None = None


# ---------------------------------------------------------------------------
# Plan diff
# ---------------------------------------------------------------------------


class PlanDiff(BaseModel):
    plan_a_id: str
    plan_b_id: str
    changed_fields: dict[str, tuple[Any, Any]]
    added_steps: list[int]      # step_numbers present in b but not a
    removed_steps: list[int]    # step_numbers present in a but not b
    changed_steps: list[int]    # step_numbers present in both but different
