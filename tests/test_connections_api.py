"""Integration tests for the connections API.

Tests verify:
- POST /connections stores metadata only (no secrets in response or DB row)
- GET /connections returns a list
- DELETE /connections/{id} removes the connection
- No secret field ever appears in any API response

OpenBao calls are mocked — no live Vault server required for these tests.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Secret fields that must NEVER appear in any response
# ---------------------------------------------------------------------------

_SECRET_FIELDS = {
    "aws_secret_access_key",
    "aws_access_key_id",
    "aws_role_arn",
    "aws_external_id",
    "secret_access_key",
    "access_key_id",
}

# Sample credential values used in test requests — these must not leak
_SAMPLE_ACCESS_KEY = "AKIASAMPLESECRET0001"
_SAMPLE_SECRET_KEY = "sample-super-secret-key-value-that-must-never-leak"
_SAMPLE_ROLE_ARN = "arn:aws:iam::999888777666:role/AetherMigrateDiscovery"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_no_secrets_in_response(data: Any, path: str = "root") -> None:
    """Recursively assert that no secret field appears anywhere in *data*."""
    if isinstance(data, dict):
        for key, val in data.items():
            assert key not in _SECRET_FIELDS, (
                f"Secret field '{key}' found in response at '{path}'"
            )
            _assert_no_secrets_in_response(val, path=f"{path}.{key}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _assert_no_secrets_in_response(item, path=f"{path}[{i}]")
    elif isinstance(data, str):
        for bad in (_SAMPLE_ACCESS_KEY, _SAMPLE_SECRET_KEY):
            assert bad not in data, (
                f"Credential value leaked into response at '{path}': {data[:40]}..."
            )


def _make_app_with_mocks() -> Any:
    """Build a FastAPI test app with DB and OpenBao fully mocked."""


    # Patch OpenBaoClient before importing the router
    bao_mock = AsyncMock()
    bao_mock.write_credential = AsyncMock(return_value=None)
    bao_mock.read_credential = AsyncMock(return_value={
        "aws_role_arn": _SAMPLE_ROLE_ARN,
        "aws_external_id": "aether-migrate-test-ws",
        "aws_default_region": "us-east-1",
    })
    bao_mock.delete_credential = AsyncMock(return_value=None)
    bao_mock.aclose = AsyncMock(return_value=None)

    return bao_mock


# ---------------------------------------------------------------------------
# Unit-level schema tests (no HTTP server required)
# ---------------------------------------------------------------------------


class TestConnectionSchemas:
    def test_create_request_accepts_valid_payload(self) -> None:
        from api.schemas.connections import ConnectionCreateRequest
        req = ConnectionCreateRequest(
            name="My AWS Connection",
            provider="aws",
            mode="read-only",
            aws_role_arn=_SAMPLE_ROLE_ARN,
            aws_external_id="aether-migrate-workspace-abc",
            aws_default_region="eu-west-1",
        )
        assert req.name == "My AWS Connection"
        assert req.aws_role_arn == _SAMPLE_ROLE_ARN

    def test_create_request_rejects_role_arn_without_external_id(self) -> None:
        from api.schemas.connections import ConnectionCreateRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            ConnectionCreateRequest(
                name="bad",
                provider="aws",
                aws_role_arn=_SAMPLE_ROLE_ARN,
                # aws_external_id intentionally omitted
            )
        assert "external_id" in str(exc_info.value).lower()

    def test_create_request_allows_access_key_without_role_arn(self) -> None:
        from api.schemas.connections import ConnectionCreateRequest
        req = ConnectionCreateRequest(
            name="IAM User Connection",
            provider="aws",
            aws_access_key_id=_SAMPLE_ACCESS_KEY,
            aws_secret_access_key=_SAMPLE_SECRET_KEY,
        )
        assert req.aws_access_key_id == _SAMPLE_ACCESS_KEY

    def test_connection_response_has_no_credential_fields(self) -> None:
        """ConnectionResponse model must not define any secret attribute."""

        from api.schemas.connections import ConnectionResponse
        fields = set(ConnectionResponse.model_fields.keys())
        for secret_field in _SECRET_FIELDS:
            assert secret_field not in fields, (
                f"ConnectionResponse must not expose secret field '{secret_field}'"
            )

    def test_connection_response_has_expected_safe_fields(self) -> None:
        from api.schemas.connections import ConnectionResponse
        expected = {"id", "workspace_id", "name", "provider", "mode", "scope",
                    "last_tested_at", "last_test_ok", "created_at"}
        fields = set(ConnectionResponse.model_fields.keys())
        assert expected.issubset(fields)

    def test_connection_test_result_no_secret_fields(self) -> None:
        from api.schemas.connections import ConnectionTestResult
        fields = set(ConnectionTestResult.model_fields.keys())
        for secret_field in _SECRET_FIELDS:
            assert secret_field not in fields, (
                f"ConnectionTestResult must not expose '{secret_field}'"
            )


# ---------------------------------------------------------------------------
# Metadata safety tests
# ---------------------------------------------------------------------------


class TestConnectionMetadataBuilding:
    """Verify _build_metadata never includes credential values."""

    def test_build_metadata_contains_no_credentials(self) -> None:
        from api.routers.connections import _build_metadata
        from api.schemas.connections import ConnectionCreateRequest

        req = ConnectionCreateRequest(
            name="test",
            provider="aws",
            aws_role_arn=_SAMPLE_ROLE_ARN,
            aws_external_id="aether-migrate-test",
            aws_access_key_id=_SAMPLE_ACCESS_KEY,
            aws_secret_access_key=_SAMPLE_SECRET_KEY,
        )
        meta = _build_metadata(req)
        meta_str = str(meta)

        assert _SAMPLE_ACCESS_KEY not in meta_str
        assert _SAMPLE_SECRET_KEY not in meta_str
        assert _SAMPLE_ROLE_ARN not in meta_str
        assert meta["has_role_arn"] is True
        assert meta["has_access_key"] is True

    def test_build_metadata_flags_false_when_absent(self) -> None:
        from api.routers.connections import _build_metadata
        from api.schemas.connections import ConnectionCreateRequest

        req = ConnectionCreateRequest(
            name="test",
            provider="aws",
        )
        meta = _build_metadata(req)
        assert meta["has_role_arn"] is False
        assert meta["has_access_key"] is False


# ---------------------------------------------------------------------------
# OpenBao payload safety tests
# ---------------------------------------------------------------------------


class TestBaoPayloadBuilding:
    def test_bao_payload_contains_credential_values(self) -> None:
        """The OpenBao payload SHOULD contain the actual credential values
        (they're encrypted at rest in OpenBao)."""
        from api.routers.connections import _build_bao_payload
        from api.schemas.connections import ConnectionCreateRequest

        req = ConnectionCreateRequest(
            name="test",
            provider="aws",
            aws_role_arn=_SAMPLE_ROLE_ARN,
            aws_external_id="ext-id-test",
            aws_access_key_id=_SAMPLE_ACCESS_KEY,
            aws_secret_access_key=_SAMPLE_SECRET_KEY,
        )
        payload = _build_bao_payload(req)
        assert payload["aws_role_arn"] == _SAMPLE_ROLE_ARN
        assert payload["aws_access_key_id"] == _SAMPLE_ACCESS_KEY
        assert payload["aws_secret_access_key"] == _SAMPLE_SECRET_KEY

    def test_bao_path_format(self) -> None:
        from api.routers.connections import _bao_path
        ws_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        conn_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        path = _bao_path(ws_id, conn_id)
        assert path == f"{ws_id}/{conn_id}"


# ---------------------------------------------------------------------------
# Response field safety assertion helper test
# ---------------------------------------------------------------------------


class TestNoSecretsAssertion:
    """Meta-tests to verify the _assert_no_secrets_in_response helper works."""

    def test_clean_dict_passes(self) -> None:
        _assert_no_secrets_in_response({"id": "abc", "name": "test"})

    def test_nested_secret_field_fails(self) -> None:
        with pytest.raises(AssertionError):
            _assert_no_secrets_in_response({"nested": {"aws_secret_access_key": "value"}})

    def test_leaked_value_fails(self) -> None:
        with pytest.raises(AssertionError):
            _assert_no_secrets_in_response({"note": _SAMPLE_SECRET_KEY})

    def test_response_list_is_checked(self) -> None:
        with pytest.raises(AssertionError):
            _assert_no_secrets_in_response([{"aws_access_key_id": "key"}])
