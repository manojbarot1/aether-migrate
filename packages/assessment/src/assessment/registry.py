"""Rule registry — auto-discovers all AssessmentRule subclasses from the rules/ package."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType

import structlog

from assessment.rule_base import AssessmentRule

log = structlog.get_logger(__name__)

_RULES_PACKAGE = "assessment.rules"


def _discover_rule_classes() -> list[type[AssessmentRule]]:
    """Import all modules in the rules/ package and collect AssessmentRule subclasses."""
    import assessment.rules as rules_pkg

    discovered: list[type[AssessmentRule]] = []
    seen: set[str] = set()

    for finder, module_name, _ in pkgutil.iter_modules(
        rules_pkg.__path__,
        prefix=f"{_RULES_PACKAGE}.",
    ):
        try:
            module: ModuleType = importlib.import_module(module_name)
        except ImportError:
            log.warning("rule_module_import_failed", module=module_name)
            continue

        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, AssessmentRule)
                and obj is not AssessmentRule
                and hasattr(obj, "rule_id")
                and obj.rule_id not in seen
            ):
                seen.add(obj.rule_id)
                discovered.append(obj)

    return discovered


class RuleRegistry:
    """Registry that holds all available assessment rules.

    Usage::

        registry = RuleRegistry.build()
        engine = AssessmentEngine(rules=registry.get_all())
    """

    def __init__(self, rule_classes: list[type[AssessmentRule]]) -> None:
        self._rules: dict[str, AssessmentRule] = {}
        for cls in rule_classes:
            instance = cls()
            self._rules[instance.rule_id] = instance

    @classmethod
    def build(cls) -> RuleRegistry:
        """Build a registry by auto-discovering all rule classes."""
        rule_classes = _discover_rule_classes()
        log.info("rule_registry_built", count=len(rule_classes))
        return cls(rule_classes)

    def get(self, rule_id: str) -> AssessmentRule | None:
        """Return the rule with the given ID, or None."""
        return self._rules.get(rule_id)

    def get_all(self) -> list[AssessmentRule]:
        """Return all registered rule instances."""
        return list(self._rules.values())

    def rule_ids(self) -> list[str]:
        """Return all registered rule IDs."""
        return sorted(self._rules.keys())

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._rules
