"""Unit tests for OpenBaoClient using httpx mock transport.

Tests verify:
- write_credential calls the correct KV v2 path
- read_credential returns the inner data dict
- Credential values are NEVER logged (monkeypatch structlog and assert)
- delete_credential calls metadata then delete endpoints
- list_credential_paths calls the LIST method
- encrypt / decrypt call the Transit API
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from secrets_svc.client import OpenBaoClient

# ---------------------------------------------------------------------------
# Fake ASGI transport for httpx
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """In-memory httpx transport: maps (method, url_suffix) → response."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()
        # Match the longest matching suffix
        for (route_method, route_path), (status, body) in self._routes.items():
            if route_method == method and path.endswith(route_path):
                return httpx.Response(
                    status,
                    headers={"content-type": "application/json"},
                    content=json.dumps(body).encode(),
                )
        return httpx.Response(404, content=b'{"errors": ["not found"]}')


def _make_client(routes: dict[tuple[str, str], tuple[int, Any]]) -> OpenBaoClient:
    """Build an OpenBaoClient with a mocked httpx transport."""
    client = OpenBaoClient(addr="https://bao.test:8200", token="test-root-token")
    client._client = httpx.AsyncClient(
        base_url="https://bao.test:8200",
        headers={"X-Vault-Token": "test-root-token"},
        transport=_MockTransport(routes),
    )
    return client


# ---------------------------------------------------------------------------
# write_credential
# ---------------------------------------------------------------------------


class TestWriteCredential:
    @pytest.mark.asyncio
    async def test_posts_to_correct_kv_v2_path(self) -> None:
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("POST", "/v1/cloud-creds/data/ws-1/conn-1"): (200, {"data": {}}),
        }
        client = _make_client(routes)
        # Should not raise
        await client.write_credential("ws-1/conn-1", {"aws_role_arn": "arn:aws:iam::123:role/R"})
        await client.aclose()

    @pytest.mark.asyncio
    async def test_wraps_data_in_kv_v2_envelope(self) -> None:
        captured: list[dict[str, Any]] = []

        class _CaptureTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                captured.append(json.loads(request.content))
                return httpx.Response(200, content=b'{"data":{}}')

        client = OpenBaoClient(addr="https://bao.test:8200", token="t")
        client._client = httpx.AsyncClient(
            base_url="https://bao.test:8200",
            transport=_CaptureTransport(),
        )
        await client.write_credential("some/path", {"key": "value"})
        await client.aclose()
        assert captured[0] == {"data": {"key": "value"}}

    @pytest.mark.asyncio
    async def test_credential_value_not_logged(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Secret values must never appear in structlog output."""
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("POST", "/v1/cloud-creds/data/ws/conn"): (200, {"data": {}}),
        }
        client = _make_client(routes)
        await client.write_credential(
            "ws/conn",
            {
                "aws_access_key_id": "AKIASECRET12345678",
                "aws_secret_access_key": "super-secret-value-that-must-not-leak",
            },
        )
        await client.aclose()
        captured = capsys.readouterr()
        assert "AKIASECRET12345678" not in captured.out
        assert "super-secret-value-that-must-not-leak" not in captured.out
        assert "super-secret-value-that-must-not-leak" not in captured.err


# ---------------------------------------------------------------------------
# read_credential
# ---------------------------------------------------------------------------


class TestReadCredential:
    @pytest.mark.asyncio
    async def test_returns_inner_data_dict(self) -> None:
        secret_data = {"aws_role_arn": "arn:aws:iam::123:role/R", "aws_default_region": "us-east-1"}
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("GET", "/v1/cloud-creds/data/ws/conn"): (
                200,
                {"data": {"data": secret_data, "metadata": {"version": 1}}},
            ),
        }
        client = _make_client(routes)
        result = await client.read_credential("ws/conn")
        await client.aclose()
        assert result == secret_data

    @pytest.mark.asyncio
    async def test_raises_on_404(self) -> None:
        routes: dict[tuple[str, str], tuple[int, Any]] = {}  # no routes → 404
        client = _make_client(routes)
        with pytest.raises(httpx.HTTPStatusError):
            await client.read_credential("ws/missing")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_credential_value_not_logged(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The returned secret value must not appear in any log output."""
        secret_data = {
            "aws_secret_access_key": "top-secret-key-must-not-appear-in-logs",
        }
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("GET", "/v1/cloud-creds/data/ws/conn"): (
                200,
                {"data": {"data": secret_data, "metadata": {"version": 1}}},
            ),
        }
        client = _make_client(routes)
        _ = await client.read_credential("ws/conn")
        await client.aclose()
        captured = capsys.readouterr()
        assert "top-secret-key-must-not-appear-in-logs" not in captured.out
        assert "top-secret-key-must-not-appear-in-logs" not in captured.err


# ---------------------------------------------------------------------------
# delete_credential
# ---------------------------------------------------------------------------


class TestDeleteCredential:
    @pytest.mark.asyncio
    async def test_fetches_metadata_then_soft_deletes(self) -> None:
        meta_resp = {
            "data": {"current_version": 3, "versions": {}},
        }
        delete_resp: dict[str, Any] = {}
        called: list[str] = []

        class _TrackTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                called.append(f"{request.method} {request.url.path}")
                if request.method == "GET" and "metadata" in request.url.path:
                    return httpx.Response(200, content=json.dumps(meta_resp).encode())
                if request.method == "POST" and "delete" in request.url.path:
                    return httpx.Response(204, content=json.dumps(delete_resp).encode())
                return httpx.Response(404, content=b"{}")

        client = OpenBaoClient(addr="https://bao.test:8200", token="t")
        client._client = httpx.AsyncClient(
            base_url="https://bao.test:8200",
            transport=_TrackTransport(),
        )
        await client.delete_credential("ws/conn")
        await client.aclose()
        assert any("GET" in c and "metadata" in c for c in called)
        assert any("POST" in c and "delete" in c for c in called)


# ---------------------------------------------------------------------------
# list_credential_paths
# ---------------------------------------------------------------------------


class TestListCredentialPaths:
    @pytest.mark.asyncio
    async def test_returns_keys_list(self) -> None:
        keys = ["conn-a", "conn-b", "conn-c"]
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("LIST", "/v1/cloud-creds/metadata/ws-1"): (200, {"data": {"keys": keys}}),
        }
        client = _make_client(routes)
        result = await client.list_credential_paths("ws-1")
        await client.aclose()
        assert result == keys


# ---------------------------------------------------------------------------
# encrypt / decrypt
# ---------------------------------------------------------------------------


class TestTransit:
    @pytest.mark.asyncio
    async def test_encrypt_returns_ciphertext(self) -> None:
        plaintext = b"hello world"
        encoded = base64.b64encode(plaintext).decode()
        ciphertext = "vault:v1:abc123"
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("POST", "/v1/transit/encrypt/aether-migrate"): (
                200,
                {"data": {"ciphertext": ciphertext}},
            ),
        }
        client = _make_client(routes)
        result = await client.encrypt(plaintext)
        await client.aclose()
        assert result == ciphertext

    @pytest.mark.asyncio
    async def test_decrypt_returns_plaintext_bytes(self) -> None:
        plaintext = b"secret data"
        encoded = base64.b64encode(plaintext).decode()
        routes: dict[tuple[str, str], tuple[int, Any]] = {
            ("POST", "/v1/transit/decrypt/aether-migrate"): (
                200,
                {"data": {"plaintext": encoded}},
            ),
        }
        client = _make_client(routes)
        result = await client.decrypt("vault:v1:abc123")
        await client.aclose()
        assert result == plaintext
