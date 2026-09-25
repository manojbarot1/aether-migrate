"""AETHER MIGRATE — IaC engine stub."""


class IaCEngine:
    """Full implementation in Phase 6.

    Generates OpenTofu modules from migration plans.  Supports AWS, Azure,
    GCP, and IBM Cloud targets.  See ADR-008.
    """

    def generate(self, plan, target_provider):
        """Generate OpenTofu modules for *plan* targeting *target_provider*."""
        raise NotImplementedError

    def validate(self, tofu_dir):
        """Run ``tofu validate`` against the modules in *tofu_dir*."""
        raise NotImplementedError
