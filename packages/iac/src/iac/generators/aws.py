"""AWS OpenTofu generator stub — Phase 2b."""

from __future__ import annotations

from typing import Any

from iac.models import TofuModule


class AWSTofuGenerator:
    """Generate OpenTofu module for AWS target (Phase 2b — not yet implemented)."""

    def generate(self, plan: Any) -> TofuModule:
        raise NotImplementedError("AWS generator not yet implemented (Phase 2b)")
