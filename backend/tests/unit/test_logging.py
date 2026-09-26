from aether.logging import REDACTED, redact, redact_text


def test_sensitive_keys_are_masked_recursively() -> None:
    out = redact(
        {
            "name": "prod",
            "secret_access_key": "abc",
            "nested": {"Password": "p", "items": [{"api_key": "k", "ok": 1}]},
        }
    )
    assert out == {
        "name": "prod",
        "secret_access_key": REDACTED,
        "nested": {"Password": REDACTED, "items": [{"api_key": REDACTED, "ok": 1}]},
    }


def test_secret_patterns_in_free_text() -> None:
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"
    text = f"failed with AKIAIOSFODNN7EXAMPLE and token {jwt}"
    out = redact_text(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "eyJhbGci" not in out
    assert out.count(REDACTED) == 2


def test_private_key_block_masked() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert redact_text(f"key={pem}") == f"key={REDACTED}"
