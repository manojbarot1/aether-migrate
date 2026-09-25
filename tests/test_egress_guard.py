"""Unit tests for EgressGuard.

Coverage:
- IP addresses are replaced in external-redacted mode
- Replacements are reversible (decrypt back to original)
- local-only mode raises error if non-Ollama model is used
- external-allowed mode passes through unchanged
"""

from __future__ import annotations

import pytest
from ai.egress import EgressGuard, EgressMode, EgressViolationError


@pytest.fixture()
def guard() -> EgressGuard:
    return EgressGuard()


# ---------------------------------------------------------------------------
# external-redacted mode
# ---------------------------------------------------------------------------


def test_ip_addresses_redacted(guard: EgressGuard) -> None:
    """IPv4 addresses must be replaced with [IP-REDACTED-N]."""
    messages = [{"role": "user", "content": "My server is at 192.168.1.100"}]
    result = guard.apply(messages, EgressMode.external_redacted, "session-1")

    assert "192.168.1.100" not in result[0]["content"]
    assert "[IP-REDACTED-" in result[0]["content"]


def test_aws_account_id_redacted(guard: EgressGuard) -> None:
    """12-digit AWS account IDs must be replaced."""
    messages = [
        {
            "role": "user",
            "content": "ARN: arn:aws:iam::123456789012:role/MyRole",
        }
    ]
    result = guard.apply(messages, EgressMode.external_redacted, "session-2")
    assert "123456789012" not in result[0]["content"]
    assert "[ACCT-REDACTED-" in result[0]["content"]


def test_hostname_redacted(guard: EgressGuard) -> None:
    """Dotted hostnames must be replaced."""
    messages = [
        {"role": "user", "content": "Connect to db.internal.example.com"}
    ]
    result = guard.apply(messages, EgressMode.external_redacted, "session-3")
    assert "db.internal.example.com" not in result[0]["content"]
    assert "[HOST-REDACTED-" in result[0]["content"]


def test_replacements_are_reversible(guard: EgressGuard) -> None:
    """Tokens must be reversible back to original values."""
    original = "Server at 10.0.0.1 and account arn:aws:iam::999888777666:user/bob"
    messages = [{"role": "user", "content": original}]
    redacted = guard.apply(messages, EgressMode.external_redacted, "rev-session")

    redacted_text = redacted[0]["content"]
    assert redacted_text != original

    restored = guard.reverse(redacted_text, "rev-session")
    assert "10.0.0.1" in restored
    assert "999888777666" in restored


def test_same_value_gets_same_token(guard: EgressGuard) -> None:
    """The same IP appearing twice must produce the same token."""
    messages = [
        {"role": "user", "content": "First: 172.16.0.1 and second: 172.16.0.1"}
    ]
    result = guard.apply(messages, EgressMode.external_redacted, "stable-session")
    content = result[0]["content"]
    # Count occurrences of the first token
    first_token = "[IP-REDACTED-1]"
    assert content.count(first_token) == 2


# ---------------------------------------------------------------------------
# local-only mode
# ---------------------------------------------------------------------------


def test_local_only_allows_ollama(guard: EgressGuard) -> None:
    """local-only mode must allow Ollama provider."""
    messages = [{"role": "user", "content": "hello"}]
    result = guard.apply(
        messages, EgressMode.local_only, "local-session", provider="ollama"
    )
    # Content passes through in local-only + ollama (uses redaction)
    assert len(result) == 1


def test_local_only_blocks_openai(guard: EgressGuard) -> None:
    """local-only mode must raise EgressViolationError for non-Ollama providers."""
    messages = [{"role": "user", "content": "hello"}]
    with pytest.raises(EgressViolationError, match="local-only"):
        guard.apply(
            messages, EgressMode.local_only, "local-session", provider="openai"
        )


def test_local_only_blocks_anthropic(guard: EgressGuard) -> None:
    """local-only mode must block Anthropic."""
    messages = [{"role": "user", "content": "hello"}]
    with pytest.raises(EgressViolationError):
        guard.apply(messages, EgressMode.local_only, "s", provider="anthropic")


# ---------------------------------------------------------------------------
# external-allowed mode
# ---------------------------------------------------------------------------


def test_external_allowed_passes_through_unchanged(guard: EgressGuard) -> None:
    """external-allowed must return messages identical to input."""
    content = "My IP is 1.2.3.4 and account 000000000001"
    messages = [{"role": "user", "content": content}]
    result = guard.apply(messages, EgressMode.external_allowed, "pass-session")
    assert result[0]["content"] == content


def test_external_allowed_no_redaction(guard: EgressGuard) -> None:
    """external-allowed must not modify any message."""
    messages = [
        {"role": "system", "content": "System: 192.0.2.0"},
        {"role": "user", "content": "Host: prod.example.com"},
    ]
    result = guard.apply(messages, EgressMode.external_allowed, "no-redact")
    assert result[0]["content"] == messages[0]["content"]
    assert result[1]["content"] == messages[1]["content"]


# ---------------------------------------------------------------------------
# Clear session
# ---------------------------------------------------------------------------


def test_clear_session_removes_maps(guard: EgressGuard) -> None:
    """clear_session must remove the token maps for the session."""
    messages = [{"role": "user", "content": "IP: 10.1.2.3"}]
    guard.apply(messages, EgressMode.external_redacted, "clear-session")

    guard.clear_session("clear-session")

    # After clearing, reverse returns the text unchanged (no tokens to reverse)
    restored = guard.reverse("[IP-REDACTED-1]", "clear-session")
    assert restored == "[IP-REDACTED-1]"
