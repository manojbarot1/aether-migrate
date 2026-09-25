"""Supported OS allowlist per target cloud provider.

Keys: ProviderName value → {"linux": [...], "windows": [...]}
Values are lower-cased substrings to match against os_name/os_version.
"""

from __future__ import annotations

from core.models import ProviderName

# ---------------------------------------------------------------------------
# Per-provider OS support lists
#
# "linux" entries match against os_name (case-insensitive substring).
# "windows" entries match against os_version (case-insensitive substring).
# ---------------------------------------------------------------------------

SUPPORTED_OS: dict[str, dict[str, list[str]]] = {
    ProviderName.azure.value: {
        "linux": [
            "rhel", "red hat", "ubuntu", "debian", "sles", "suse",
            "centos", "oracle linux", "flatcar", "alma", "rocky",
        ],
        "windows": ["2016", "2019", "2022", "11", "10"],
    },
    ProviderName.aws.value: {
        "linux": [
            "rhel", "red hat", "ubuntu", "debian", "sles", "suse",
            "centos", "amazon linux", "al2", "oracle linux", "alma", "rocky",
        ],
        "windows": ["2016", "2019", "2022", "11", "10"],
    },
    ProviderName.gcp.value: {
        "linux": [
            "rhel", "red hat", "ubuntu", "debian", "sles", "suse",
            "centos", "cos", "rocky", "alma",
        ],
        "windows": ["2016", "2019", "2022", "11", "10"],
    },
    ProviderName.ibm.value: {
        "linux": [
            "rhel", "red hat", "ubuntu", "debian", "sles", "suse",
            "centos", "alma", "rocky",
        ],
        "windows": ["2016", "2019", "2022", "11", "10"],
    },
}
