"""AETHER MIGRATE — disk type mapping table.

Maps source provider disk types to equivalent types on target providers.
Used by the cost engine when estimating storage costs during migration.

Mapping philosophy:
  - Match on performance tier (capacity, IOPS, throughput)
  - Err on the side of the slightly better tier (no downgrade by default)
  - Mapping is directional: source → target provider

Structure: DISK_MAPPING[source_type] = {provider: target_type}
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical disk type cross-map
# ---------------------------------------------------------------------------
#
# Source types (left column) can be any provider's disk type string.
# Target types are keyed by provider name.
#
# Tier groupings:
#   tier-1: Standard HDD          (AWS: sc1/st1, Azure: Standard_HDD, GCP: pd-standard)
#   tier-2: Standard SSD          (AWS: gp2, Azure: Standard_SSD, GCP: pd-balanced)
#   tier-3: Premium SSD           (AWS: gp3/io1, Azure: Premium_SSD, GCP: pd-ssd)
#   tier-4: Ultra/Extreme SSD     (AWS: io2, Azure: Premium_SSD_v2/Ultra, GCP: hyperdisk-extreme)

DISK_MAPPING: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------
    # AWS source types → Azure / GCP equivalents
    # ------------------------------------------------------------------
    "gp3": {
        "azure": "premium-ssd-v2",
        "gcp": "pd-ssd",
        "ibm": "custom",
    },
    "gp2": {
        "azure": "premium-ssd",
        "gcp": "pd-balanced",
        "ibm": "general-purpose",
    },
    "io1": {
        "azure": "premium-ssd-v2",
        "gcp": "pd-ssd",
        "ibm": "custom",
    },
    "io2": {
        "azure": "ultra",
        "gcp": "hyperdisk-extreme",
        "ibm": "ultra",
    },
    "st1": {
        "azure": "standard-hdd",
        "gcp": "pd-standard",
        "ibm": "standard",
    },
    "sc1": {
        "azure": "standard-hdd",
        "gcp": "pd-standard",
        "ibm": "standard",
    },
    # ------------------------------------------------------------------
    # Azure source types → AWS / GCP equivalents
    # ------------------------------------------------------------------
    "premium-ssd-v2": {
        "aws": "gp3",
        "gcp": "pd-ssd",
        "ibm": "custom",
    },
    "premium-ssd": {
        "aws": "gp3",
        "gcp": "pd-balanced",
        "ibm": "general-purpose",
    },
    "premium_lrs": {
        "aws": "gp3",
        "gcp": "pd-balanced",
        "ibm": "general-purpose",
    },
    "standard-ssd": {
        "aws": "gp2",
        "gcp": "pd-balanced",
        "ibm": "general-purpose",
    },
    "standard-hdd": {
        "aws": "st1",
        "gcp": "pd-standard",
        "ibm": "standard",
    },
    "ultra": {
        "aws": "io2",
        "gcp": "hyperdisk-extreme",
        "ibm": "ultra",
    },
    # ------------------------------------------------------------------
    # GCP source types
    # ------------------------------------------------------------------
    "pd-ssd": {
        "aws": "gp3",
        "azure": "premium-ssd-v2",
        "ibm": "custom",
    },
    "pd-balanced": {
        "aws": "gp2",
        "azure": "premium-ssd",
        "ibm": "general-purpose",
    },
    "pd-standard": {
        "aws": "st1",
        "azure": "standard-hdd",
        "ibm": "standard",
    },
    "hyperdisk-extreme": {
        "aws": "io2",
        "azure": "ultra",
        "ibm": "ultra",
    },
}


def map_disk_type(source_type: str, target_provider: str) -> str:
    """Return the target disk type for *source_type* on *target_provider*.

    Falls back to a sensible default if no explicit mapping exists.
    Normalizes input to lowercase with hyphens.
    """
    normalized = source_type.lower().replace("_", "-")
    mapping = DISK_MAPPING.get(normalized, {})
    result = mapping.get(target_provider.lower())

    if result is None:
        # Default fallback by tier heuristic
        lower = normalized
        if any(k in lower for k in ("ultra", "io2", "extreme")):
            result = "ultra" if target_provider == "azure" else "io2"
        elif any(k in lower for k in ("premium", "gp3", "io1", "ssd")):
            result = "premium-ssd-v2" if target_provider == "azure" else "gp3"
        else:
            result = "standard-hdd" if target_provider == "azure" else "st1"

    return result
