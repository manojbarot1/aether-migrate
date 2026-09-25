"""EgressGuard — data egress control for LLM calls.

Enforces three egress modes:
- ``external-allowed``: pass messages through unchanged.
- ``external-redacted``: replace IPs, hostnames, and AWS account IDs with
  reversible tokens before sending to an external LLM.
- ``local-only``: only Ollama (local) providers are permitted; raises
  ``EgressViolationError`` for any other provider.

The replacement map is keyed by session/conversation ID so tokens can be
un-redacted when displaying results to the user.
"""

from __future__ import annotations

import re
from collections import defaultdict
from enum import Enum


class EgressMode(str, Enum):  # noqa: UP042 — StrEnum requires Python 3.11; compat kept
    external_allowed = "external-allowed"
    external_redacted = "external-redacted"
    local_only = "local-only"


class EgressViolationError(Exception):
    """Raised when egress mode prevents a non-local provider call."""


# ---------------------------------------------------------------------------
# Regex patterns for redactable tokens
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(r"\b(\d{1,3}\.){3}\d{1,3}\b")

# Hostname heuristic: 2+ dot-separated segments that look like a hostname
# (at least one alpha char per segment, not a version string like 1.2.3)
_HOSTNAME_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)"
    r"{1,}[a-zA-Z]{2,}\b"
)

# AWS account ID: standalone 12-digit numbers that look like AWS account IDs.
# Matches exactly 12 consecutive digits not adjacent to other digits.
_AWS_ACCT_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")


class EgressGuard:
    """Apply data-egress controls to chat messages.

    Each instance maintains a per-session reversible replacement map so the
    UI can un-redact tool results after they have been returned from the LLM.

    Usage::

        guard = EgressGuard()
        redacted_msgs = guard.apply(messages, EgressMode.external_redacted, session_id)
        original_text = guard.reverse(redacted_text, session_id)
    """

    def __init__(self) -> None:
        # session_id -> {token -> original}
        self._reverse_maps: dict[str, dict[str, str]] = defaultdict(dict)
        # session_id -> {original -> token}  (for stable repeated replacement)
        self._forward_maps: dict[str, dict[str, str]] = defaultdict(dict)

    def apply(
        self,
        messages: list[dict[str, str]],
        mode: EgressMode,
        session_id: str,
        provider: str = "openai",
    ) -> list[dict[str, str]]:
        """Return a (possibly redacted) copy of *messages*.

        Parameters
        ----------
        messages:   List of ``{"role": ..., "content": ...}`` dicts.
        mode:       The egress mode to enforce.
        session_id: Identifies the reversible-map bucket for this session.
        provider:   The LLM provider about to receive the messages.
        """
        if mode == EgressMode.local_only and provider.lower() != "ollama":
            raise EgressViolationError(
                f"Egress mode 'local-only' forbids non-Ollama provider '{provider}'"
            )
        if mode == EgressMode.external_allowed:
            return messages
        # external-redacted (also applied for local_only with ollama)
        return [
            {**msg, "content": self._redact(msg.get("content") or "", session_id)}
            if "content" in msg
            else msg
            for msg in messages
        ]

    def reverse(self, text: str, session_id: str) -> str:
        """Replace all redaction tokens in *text* with their original values."""
        result = text
        for token, original in self._reverse_maps[session_id].items():
            result = result.replace(token, original)
        return result

    def clear_session(self, session_id: str) -> None:
        """Remove all maps for *session_id* (call after conversation ends)."""
        self._reverse_maps.pop(session_id, None)
        self._forward_maps.pop(session_id, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _redact(self, text: str, session_id: str) -> str:
        text = self._replace_pattern(text, _IPV4_RE, "IP", session_id)
        text = self._replace_pattern(text, _AWS_ACCT_RE, "ACCT", session_id)
        text = self._replace_pattern(text, _HOSTNAME_RE, "HOST", session_id)
        return text

    def _replace_pattern(
        self,
        text: str,
        pattern: re.Pattern[str],
        prefix: str,
        session_id: str,
    ) -> str:
        fwd = self._forward_maps[session_id]
        rev = self._reverse_maps[session_id]

        def _replace(match: re.Match[str]) -> str:
            original = match.group(0)
            if original in fwd:
                return fwd[original]
            n = sum(1 for t in fwd.values() if t.startswith(f"[{prefix}-REDACTED-"))
            token = f"[{prefix}-REDACTED-{n + 1}]"
            fwd[original] = token
            rev[token] = original
            return token

        return pattern.sub(_replace, text)
