"""Plan diff utility for AETHER MIGRATE."""

from __future__ import annotations

from typing import Any

from planner.models import MigrationPlan, PlanDiff


def diff_plans(plan_a: MigrationPlan, plan_b: MigrationPlan) -> PlanDiff:
    """Return a structured diff between two plan versions.

    Compares top-level scalar fields and the steps list.
    """
    changed_fields: dict[str, tuple[Any, Any]] = {}

    # Compare scalar / list fields (exclude steps and content_hash)
    _SCALAR_FIELDS = [
        "name",
        "version",
        "target_provider",
        "target_region",
        "downtime_estimate_minutes",
        "downtime_basis",
        "rollback_plan",
        "catalog_version",
        "prerequisites",
        "assumptions",
        "acknowledged_findings",
    ]

    for field in _SCALAR_FIELDS:
        val_a = getattr(plan_a, field, None)
        val_b = getattr(plan_b, field, None)
        if val_a != val_b:
            changed_fields[field] = (val_a, val_b)

    # Compare steps by step_number
    steps_a = {s.step_number: s for s in plan_a.steps}
    steps_b = {s.step_number: s for s in plan_b.steps}

    added_steps = sorted(set(steps_b) - set(steps_a))
    removed_steps = sorted(set(steps_a) - set(steps_b))
    changed_steps = [
        num
        for num in sorted(set(steps_a) & set(steps_b))
        if steps_a[num].model_dump() != steps_b[num].model_dump()
    ]

    return PlanDiff(
        plan_a_id=plan_a.plan_id,
        plan_b_id=plan_b.plan_id,
        changed_fields=changed_fields,
        added_steps=added_steps,
        removed_steps=removed_steps,
        changed_steps=changed_steps,
    )
