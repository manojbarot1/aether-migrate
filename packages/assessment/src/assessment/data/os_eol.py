"""OS end-of-life dates lookup table.

Keyed by (os_family_lower, os_version_lower) tuples.
Values are ISO date strings ("YYYY-MM-DD").

These dates are best-effort; rules must treat absent entries as
"EOL unknown" rather than "still supported".
"""

from __future__ import annotations

from datetime import date

# (os_family, os_version) → EOL date
# os_family is matched case-insensitively against the full os_name.
OS_EOL: dict[tuple[str, str], date] = {
    # Windows Server
    ("windows", "2003"): date(2015, 7, 14),
    ("windows", "2008"): date(2020, 1, 14),
    ("windows", "2008 r2"): date(2020, 1, 14),
    ("windows", "2012"): date(2023, 10, 10),
    ("windows", "2012 r2"): date(2023, 10, 10),
    ("windows", "2016"): date(2027, 1, 12),
    ("windows", "2019"): date(2029, 1, 9),
    ("windows", "2022"): date(2031, 10, 14),
    # Ubuntu
    ("ubuntu", "14.04"): date(2019, 4, 30),
    ("ubuntu", "16.04"): date(2021, 4, 30),
    ("ubuntu", "18.04"): date(2023, 4, 30),
    ("ubuntu", "20.04"): date(2025, 4, 30),
    ("ubuntu", "22.04"): date(2027, 4, 30),
    ("ubuntu", "24.04"): date(2029, 4, 30),
    # RHEL / CentOS
    ("rhel", "6"): date(2020, 11, 30),
    ("rhel", "7"): date(2024, 6, 30),
    ("rhel", "8"): date(2029, 5, 31),
    ("rhel", "9"): date(2032, 5, 31),
    ("centos", "6"): date(2020, 11, 30),
    ("centos", "7"): date(2024, 6, 30),
    ("centos", "8"): date(2021, 12, 31),
    # Debian
    ("debian", "9"): date(2022, 6, 30),
    ("debian", "10"): date(2024, 6, 30),
    ("debian", "11"): date(2026, 6, 30),
    ("debian", "12"): date(2028, 6, 30),
    # SLES
    ("sles", "12"): date(2024, 10, 31),
    ("sles", "15"): date(2031, 7, 31),
}


def lookup_eol(os_name: str, os_version: str) -> date | None:
    """Return the EOL date for *os_name* / *os_version*, or None if unknown."""
    os_name_lower = os_name.lower()
    os_version_lower = os_version.lower()

    # Try exact tuple first
    if (os_name_lower, os_version_lower) in OS_EOL:
        return OS_EOL[(os_name_lower, os_version_lower)]

    # Try matching on os_name substring (e.g. "Red Hat Enterprise Linux 7" → "rhel")
    family_map = {
        "red hat": "rhel",
        "centos": "centos",
        "ubuntu": "ubuntu",
        "debian": "debian",
        "sles": "sles",
        "suse linux": "sles",
        "windows": "windows",
    }
    matched_family = None
    for key, family in family_map.items():
        if key in os_name_lower:
            matched_family = family
            break

    if matched_family is None:
        return None

    # Try matching version prefix (e.g. "2012 R2" contains "2012")
    for (fam, ver), eol_date in OS_EOL.items():
        if fam == matched_family and (ver in os_version_lower or os_version_lower in ver):
            return eol_date

    return None
