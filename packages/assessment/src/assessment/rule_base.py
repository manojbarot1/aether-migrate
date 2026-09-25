"""Abstract base class for all AETHER MIGRATE assessment rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from core.models import ProviderName, VMSpec

from assessment.models import FindingRecord, RuleSeverity

if TYPE_CHECKING:
    from assessment.engine import CatalogContext


class AssessmentRule(ABC):
    """Base class for all migration-readiness rules.

    Concrete rules must define class-level attributes and implement ``check()``.

    Rules are pure functions: ``check()`` must not perform network calls or
    database queries. All catalog data is accessed via the ``CatalogContext``
    argument which is pre-loaded before rules run.
    """

    rule_id: str
    rule_version: str = "1.0"
    severity: RuleSeverity
    applies_to: Literal["source", "target", "both"]
    title: str

    @abstractmethod
    def check(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        catalog: CatalogContext,
    ) -> FindingRecord | None:
        """Evaluate the rule.

        Returns a ``FindingRecord`` if the rule fires, ``None`` if the VM
        passes the check. The ``evidence`` dict must contain the actual field
        values that caused the rule to fire (not just field names).
        """
