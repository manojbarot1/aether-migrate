"""Secret redaction for audit log argument sanitisation.

``redact_dict`` recursively walks a dictionary and replaces values whose
keys match any of the sensitive patterns with ``***REDACTED***``.

Rules:
- Key matching is case-insensitive.
- Nested dicts are walked recursively.
- Lists are walked element-by-element (dicts inside lists are also redacted).
- Non-string, non-dict, non-list values are left as-is unless their key matches.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# Patterns matched against dict key names (case-insensitive, full word / suffix)
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"passwd", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"api[-_]?key", re.IGNORECASE),
    re.compile(r"access[-_]?key", re.IGNORECASE),
    re.compile(r"private[-_]?key", re.IGNORECASE),
    re.compile(r"client[-_]?secret", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
    re.compile(r"aws[-_]secret", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"auth", re.IGNORECASE),
    re.compile(r"passphrase", re.IGNORECASE),
    re.compile(r"private_access_token", re.IGNORECASE),
]


def _is_sensitive(key: str) -> bool:
    """Return True if *key* matches any sensitive pattern."""
    return any(pattern.search(key) for pattern in _SENSITIVE_PATTERNS)


def redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *d* with sensitive values replaced by ``***REDACTED***``.

    The original dict is not modified.
    """
    return _redact_value(d)  # type: ignore[return-value]


def _redact_value(value: Any, parent_key: str = "") -> Any:
    """Recursively redact sensitive values."""
    if isinstance(value, dict):
        return {k: _redact_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, parent_key) for item in value]
    # Leaf value — redact if the parent key is sensitive
    if parent_key and _is_sensitive(parent_key):
        return REDACTED
    return value
