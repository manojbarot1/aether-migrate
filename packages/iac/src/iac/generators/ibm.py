"""IBM Cloud OpenTofu generator stub."""

from __future__ import annotations

from typing import Any

from iac.models import TofuModule


class IBMTofuGenerator:
    """Generate OpenTofu module for IBM Cloud target (not yet implemented)."""

    def generate(self, plan: Any) -> TofuModule:
        raise NotImplementedError("IBM Cloud generator not yet implemented")
