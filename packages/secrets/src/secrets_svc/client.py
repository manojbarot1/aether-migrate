"""OpenBao credential client for AETHER MIGRATE.

Wraps the OpenBao (Vault-compatible) HTTP API for:
- KV v2  — cloud credential storage under ``cloud-creds/{workspace_id}/{connection_id}``
- Transit — encrypt / decrypt arbitrary bytes without exposing key material

Security guarantees:
- Credential *values* are NEVER logged. Only paths are logged.
- Token is read from ``/run/secrets/openbao_token`` (production) or
  ``OPENBAO_TOKEN`` env var (dev/test only).
- ``httpx.AsyncClient`` with explicit connect + read timeouts — no infinite waits.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_CONNECT_TIMEOUT = 10.0   # seconds
_READ_TIMEOUT = 30.0      # seconds

_TOKEN_FILE = Path("/run/secrets/openbao_token")
_KV_MOUNT = "cloud-creds"
_TRANSIT_KEY = "aether-migrate"


def _load_token() -> str:
    """Read the Vault token.

    Priority:
    1. ``/run/secrets/openbao_token`` file (Docker / Kubernetes secret mount)
    2. ``OPENBAO_TOKEN`` environment variable (dev / CI only)

    Raises ``RuntimeError`` if neither source is available.
    """
    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text().strip()
        if token:
            return token
    env_token = os.environ.get("OPENBAO_TOKEN", "")
    if env_token:
        return env_token
    raise RuntimeError(
        "OpenBao token not found: set OPENBAO_TOKEN env var or mount a token at "
        f"{_TOKEN_FILE}"
    )


class OpenBaoClient:
    """Async client for OpenBao (Vault-compatible) KV v2 and Transit APIs.

    Parameters
    ----------
    addr:
        Base URL of the OpenBao server, e.g. ``https://bao.internal:8200``.
        Falls back to the ``OPENBAO_ADDR`` environment variable when *addr* is
        not supplied.
    token:
        Vault token.  When omitted the client calls ``_load_token()`` which
        reads the secret mount file first, then the ``OPENBAO_TOKEN`` env var.
    """

    def __init__(self, addr: str | None = None, token: str | None = None) -> None:
        self._addr = (addr or os.environ.get("OPENBAO_ADDR", "")).rstrip("/")
        if not self._addr:
            raise ValueError(
                "OpenBao address not configured: pass addr= or set OPENBAO_ADDR"
            )
        self._token = token or _load_token()
        self._client = httpx.AsyncClient(
            base_url=self._addr,
            headers={"X-Vault-Token": self._token},
            timeout=httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=10.0, pool=5.0),
        )

    # ------------------------------------------------------------------
    # KV v2 helpers
    # ------------------------------------------------------------------

    def _kv_data_url(self, path: str) -> str:
        return f"/v1/{_KV_MOUNT}/data/{path}"

    def _kv_metadata_url(self, path: str) -> str:
        return f"/v1/{_KV_MOUNT}/metadata/{path}"

    def _kv_delete_url(self, path: str) -> str:
        # KV v2 soft-delete uses the ``delete`` sub-path
        return f"/v1/{_KV_MOUNT}/delete/{path}"

    # ------------------------------------------------------------------
    # Public KV v2 API
    # ------------------------------------------------------------------

    async def write_credential(self, path: str, data: dict[str, Any]) -> None:
        """Store *data* at *path* using KV v2 ``POST /v1/{mount}/data/{path}``.

        Only *path* is logged — credential values are never emitted.
        """
        log.info("openbao.write_credential", path=path)
        resp = await self._client.post(
            self._kv_data_url(path),
            json={"data": data},
        )
        _raise_for_status(resp, f"write_credential path={path}")

    async def read_credential(self, path: str) -> dict[str, Any]:
        """Read credential data from *path* (KV v2 ``GET``).

        Returns the inner ``data`` dict from the KV v2 response envelope.
        Only *path* is logged.
        """
        log.info("openbao.read_credential", path=path)
        resp = await self._client.get(self._kv_data_url(path))
        _raise_for_status(resp, f"read_credential path={path}")
        body: dict[str, Any] = resp.json()
        return body["data"]["data"]

    async def delete_credential(self, path: str) -> None:
        """Soft-delete the latest version of the secret at *path* (KV v2).

        This marks the version deleted but keeps metadata, allowing recovery.
        Only *path* is logged.
        """
        log.info("openbao.delete_credential", path=path)
        # KV v2 soft-delete: POST to /delete endpoint with {"versions": [latest]}
        # We first fetch the current version then delete it.
        meta_resp = await self._client.get(self._kv_metadata_url(path))
        _raise_for_status(meta_resp, f"delete_credential metadata path={path}")
        meta: dict[str, Any] = meta_resp.json()
        current_version: int = meta["data"]["current_version"]
        resp = await self._client.post(
            self._kv_delete_url(path),
            json={"versions": [current_version]},
        )
        _raise_for_status(resp, f"delete_credential path={path}")

    async def list_credential_paths(self, prefix: str) -> list[str]:
        """List secret keys under *prefix* (KV v2 LIST).

        Returns bare key names (not full paths).
        Only *prefix* is logged.
        """
        log.info("openbao.list_credential_paths", prefix=prefix)
        resp = await self._client.request(
            "LIST",
            self._kv_metadata_url(prefix),
        )
        _raise_for_status(resp, f"list_credential_paths prefix={prefix}")
        body: dict[str, Any] = resp.json()
        return body["data"]["keys"]

    # ------------------------------------------------------------------
    # Transit API
    # ------------------------------------------------------------------

    async def encrypt(self, plaintext: bytes) -> str:
        """Encrypt *plaintext* using the Transit engine.

        Returns the Base64-encoded ciphertext token (``vault:v1:...`` format).
        """
        log.info("openbao.encrypt")
        encoded = base64.b64encode(plaintext).decode("ascii")
        resp = await self._client.post(
            f"/v1/transit/encrypt/{_TRANSIT_KEY}",
            json={"plaintext": encoded},
        )
        _raise_for_status(resp, "encrypt")
        body: dict[str, Any] = resp.json()
        return body["data"]["ciphertext"]

    async def decrypt(self, ciphertext: str) -> bytes:
        """Decrypt a ciphertext token produced by :meth:`encrypt`.

        Returns the original plaintext bytes.
        """
        log.info("openbao.decrypt")
        resp = await self._client.post(
            f"/v1/transit/decrypt/{_TRANSIT_KEY}",
            json={"ciphertext": ciphertext},
        )
        _raise_for_status(resp, "decrypt")
        body: dict[str, Any] = resp.json()
        return base64.b64decode(body["data"]["plaintext"])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient``."""
        await self._client.aclose()

    async def __aenter__(self) -> OpenBaoClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _raise_for_status(resp: httpx.Response, context: str) -> None:
    """Raise ``httpx.HTTPStatusError`` with a sanitised message on 4xx/5xx.

    The raw response body is logged at DEBUG to aid debugging, but is NOT
    included in the raised exception message to prevent accidental leakage
    in caller error handling / logs.
    """
    if resp.is_success:
        return
    log.debug(
        "openbao.http_error",
        context=context,
        status_code=resp.status_code,
    )
    resp.raise_for_status()
