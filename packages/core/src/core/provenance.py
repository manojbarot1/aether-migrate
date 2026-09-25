"""Provenance tracking helpers for AETHER MIGRATE.

``ProvenanceTracker`` is a lightweight helper that accumulates per-field
provenance metadata while building a ``VMSpec`` (or any dict that will
become a ``Resource`` sub-model).
"""

from __future__ import annotations

from core.models import ProvenanceField, ProvenanceKind


class ProvenanceTracker:
    """Accumulates provenance annotations for individual model fields.

    Usage::

        tracker = ProvenanceTracker()
        tracker.mark("vcpu", ProvenanceKind.discovered, source="ec2:describe_instances")
        tracker.mark("memory_gib", ProvenanceKind.discovered, source="ec2:describe_instances")
        tracker.mark("cpu_p95", ProvenanceKind.inferred, confidence=0.8, source="cloudwatch")

        spec = VMSpec(..., provenance=tracker.build())
    """

    def __init__(self) -> None:
        self._fields: dict[str, ProvenanceField] = {}

    def mark(
        self,
        field: str,
        kind: ProvenanceKind,
        *,
        confidence: float = 1.0,
        source: str = "",
    ) -> ProvenanceTracker:
        """Record provenance for *field*.

        Returns self for method chaining.
        """
        self._fields[field] = ProvenanceField(
            kind=kind,
            confidence=confidence,
            source=source,
        )
        return self

    def mark_all(
        self,
        fields: list[str],
        kind: ProvenanceKind,
        *,
        confidence: float = 1.0,
        source: str = "",
    ) -> ProvenanceTracker:
        """Record the same provenance for every field in *fields*."""
        for field in fields:
            self.mark(field, kind, confidence=confidence, source=source)
        return self

    def build(self) -> dict[str, ProvenanceField]:
        """Return a copy of the accumulated provenance mapping."""
        return dict(self._fields)

    def get(self, field: str) -> ProvenanceField | None:
        """Return the ``ProvenanceField`` for *field*, or ``None`` if absent."""
        return self._fields.get(field)

    def __len__(self) -> int:
        return len(self._fields)

    def __repr__(self) -> str:
        return f"ProvenanceTracker(fields={list(self._fields.keys())})"
