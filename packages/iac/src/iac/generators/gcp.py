"""GCP OpenTofu generator stub."""

from __future__ import annotations

from typing import Any

from iac.models import TofuModule


class GCPTofuGenerator:
    """Generate OpenTofu module for GCP target (not yet implemented)."""

    def generate(self, plan: Any) -> TofuModule:
        raise NotImplementedError("GCP generator not yet implemented")
