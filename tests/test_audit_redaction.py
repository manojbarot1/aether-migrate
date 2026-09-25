"""Tests for secret redaction in audit log argument sanitisation."""

from __future__ import annotations

from audit.redaction import REDACTED, redact_dict

# ---------------------------------------------------------------------------
# Basic key redaction
# ---------------------------------------------------------------------------


def test_password_key_is_redacted() -> None:
    result = redact_dict({"password": "supersecret"})
    assert result["password"] == REDACTED


def test_secret_key_is_redacted() -> None:
    result = redact_dict({"client_secret": "abc123"})
    assert result["client_secret"] == REDACTED


def test_token_key_is_redacted() -> None:
    result = redact_dict({"access_token": "eyJhbGciOiJSUzI1NiJ9..."})
    assert result["access_token"] == REDACTED


def test_api_key_is_redacted() -> None:
    result = redact_dict({"api_key": "sk-1234567890"})
    assert result["api_key"] == REDACTED


def test_bearer_key_is_redacted() -> None:
    result = redact_dict({"bearer": "some-token-value"})
    assert result["bearer"] == REDACTED


def test_aws_secret_key_is_redacted() -> None:
    result = redact_dict({"aws_secret_access_key": "wJalrXUtnFEMI"})
    assert result["aws_secret_access_key"] == REDACTED


def test_private_key_is_redacted() -> None:
    result = redact_dict({"private_key": "-----BEGIN RSA PRIVATE KEY-----"})
    assert result["private_key"] == REDACTED


# ---------------------------------------------------------------------------
# Non-sensitive keys pass through
# ---------------------------------------------------------------------------


def test_non_sensitive_key_passes_through() -> None:
    result = redact_dict({"region": "us-east-1", "account_id": "123456789012"})
    assert result["region"] == "us-east-1"
    assert result["account_id"] == "123456789012"


def test_mixed_dict_redacts_only_sensitive() -> None:
    data = {
        "provider": "aws",
        "region": "eu-west-1",
        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "account_id": "123456789012",
    }
    result = redact_dict(data)

    assert result["provider"] == "aws"
    assert result["region"] == "eu-west-1"
    assert result["account_id"] == "123456789012"
    assert result["secret_access_key"] == REDACTED


# ---------------------------------------------------------------------------
# Nested dicts
# ---------------------------------------------------------------------------


def test_nested_secret_key_is_redacted() -> None:
    data = {
        "connection": {
            "provider": "azure",
            "client_id": "my-app",
            "client_secret": "super-secret",
        }
    }
    result = redact_dict(data)

    assert result["connection"]["client_id"] == "my-app"
    assert result["connection"]["client_secret"] == REDACTED


def test_deeply_nested_secret_is_redacted() -> None:
    data = {
        "level1": {
            "level2": {
                "level3": {
                    "password": "deep-secret"
                }
            }
        }
    }
    result = redact_dict(data)
    assert result["level1"]["level2"]["level3"]["password"] == REDACTED


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_list_of_dicts_are_redacted() -> None:
    data = {
        "connections": [
            {"name": "conn1", "token": "tok1"},
            {"name": "conn2", "token": "tok2"},
        ]
    }
    result = redact_dict(data)

    assert result["connections"][0]["name"] == "conn1"
    assert result["connections"][0]["token"] == REDACTED
    assert result["connections"][1]["token"] == REDACTED


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_dict_returns_empty() -> None:
    assert redact_dict({}) == {}


def test_non_string_values_preserved_for_safe_keys() -> None:
    data = {"count": 42, "enabled": True, "ratio": 3.14}
    result = redact_dict(data)
    assert result["count"] == 42
    assert result["enabled"] is True
    assert result["ratio"] == 3.14


def test_original_dict_not_mutated() -> None:
    original = {"password": "secret", "name": "test"}
    original_copy = dict(original)
    redact_dict(original)
    assert original == original_copy


def test_case_insensitive_key_matching() -> None:
    """Key matching must be case-insensitive."""
    result = redact_dict({"PASSWORD": "secret", "Secret": "value", "API_KEY": "key123"})
    assert result["PASSWORD"] == REDACTED
    assert result["Secret"] == REDACTED
    assert result["API_KEY"] == REDACTED
