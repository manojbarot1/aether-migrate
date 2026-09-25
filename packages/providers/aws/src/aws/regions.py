"""Static fallback list of AWS regions.

Used when ``ec2.describe_regions()`` fails (e.g. denied) so that the
workflow can still attempt discovery with a reasonable default set.

The list is current as of 2025-07. It covers all GA opt-in-not-required
and commonly opted-in regions.  New regions added by AWS after this date
will only appear if the describe_regions call succeeds.
"""

from __future__ import annotations

# fmt: off
_STANDARD_REGIONS: list[str] = [
    # US
    "us-east-1",       # US East (N. Virginia)
    "us-east-2",       # US East (Ohio)
    "us-west-1",       # US West (N. California)
    "us-west-2",       # US West (Oregon)
    # Europe
    "eu-west-1",       # Europe (Ireland)
    "eu-west-2",       # Europe (London)
    "eu-west-3",       # Europe (Paris)
    "eu-central-1",    # Europe (Frankfurt)
    "eu-central-2",    # Europe (Zurich)
    "eu-north-1",      # Europe (Stockholm)
    "eu-south-1",      # Europe (Milan)
    "eu-south-2",      # Europe (Spain)
    # Asia Pacific
    "ap-east-1",       # Asia Pacific (Hong Kong)
    "ap-northeast-1",  # Asia Pacific (Tokyo)
    "ap-northeast-2",  # Asia Pacific (Seoul)
    "ap-northeast-3",  # Asia Pacific (Osaka)
    "ap-southeast-1",  # Asia Pacific (Singapore)
    "ap-southeast-2",  # Asia Pacific (Sydney)
    "ap-southeast-3",  # Asia Pacific (Jakarta)
    "ap-southeast-4",  # Asia Pacific (Melbourne)
    "ap-southeast-5",  # Asia Pacific (Malaysia)
    "ap-south-1",      # Asia Pacific (Mumbai)
    "ap-south-2",      # Asia Pacific (Hyderabad)
    # Middle East
    "me-central-1",    # Middle East (UAE)
    "me-south-1",      # Middle East (Bahrain)
    # Africa
    "af-south-1",      # Africa (Cape Town)
    # Canada
    "ca-central-1",    # Canada (Central)
    "ca-west-1",       # Canada (Calgary)
    # South America
    "sa-east-1",       # South America (São Paulo)
    # Israel
    "il-central-1",    # Israel (Tel Aviv)
]
# fmt: on


def list_aws_regions() -> list[str]:
    """Return the static fallback list of standard AWS region names."""
    return list(_STANDARD_REGIONS)
