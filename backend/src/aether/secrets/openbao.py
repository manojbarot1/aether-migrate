"""Minimal async OpenBao (Vault-API-compatible) client.

Only the operations the platform needs: AppRole login, KV v2 write/read/delete.
Which of these a given service can actually perform is decided by its OpenBao
policy, not by this code:

* ``aether-api``       — create/update/delete connection secrets, **cannot read** them.
* ``aether-connector`` — read connection secrets, cannot write them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from aether.config import Settings
from aether.core.errors import UpstreamError
from aether.logging import get_logger

log = get_logger(__name__)


class OpenBaoError(UpstreamError):
    def __init__(self, public_message: str, status: int | None = None) -> None:
        super().__init__(public_message)
        self.status = status


class OpenBaoClient:
    def __init__(
        self,
        addr: str,
        role_id: str,
        secret_id: str,
        kv_mount: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=addr, timeout=10.0, transport=transport)
        self._role_id = role_id
        self._secret_id = secret_id
        self._kv = kv_mount.strip("/")
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenBaoClient:
        role_id = settings.secret(settings.bao_role_id_file)
        secret_id = settings.secret(settings.bao_secret_id_file)
        if role_id is None or secret_id is None:
            raise RuntimeError("OpenBao AppRole credentials are not mounted")
        return cls(
            settings.bao_addr, role_id.get_secret_value(), secret_id.get_secret_value(), settings.bao_kv_mount
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _login(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._token_expires - 30:
                return self._token
            resp = await self._http.post(
                "/v1/auth/approle/login", json={"role_id": self._role_id, "secret_id": self._secret_id}
            )
            if resp.status_code != 200:
                raise OpenBaoError("secret store login failed", resp.status_code)
            auth = resp.json()["auth"]
            self._token = auth["client_token"]
            self._token_expires = time.monotonic() + float(auth.get("lease_duration") or 300)
            return self._token

    async def _request(self, method: str, path: str, json: Any = None) -> httpx.Response:
        for attempt in (1, 2):
            token = await self._login()
            resp = await self._http.request(method, path, json=json, headers={"X-Vault-Token": token})
            if resp.status_code == 403 and attempt == 1:
                # Token may have been revoked or expired early; re-login once.
                self._token = None
                continue
            return resp
        return resp

    async def health(self) -> dict[str, Any]:
        resp = await self._http.get("/v1/sys/health", params={"standbyok": "true"})
        data: dict[str, Any] = resp.json() if resp.content else {}
        data["http_status"] = resp.status_code
        return data

    async def kv_write(self, path: str, data: dict[str, str]) -> int:
        resp = await self._request("POST", f"/v1/{self._kv}/data/{path}", json={"data": data})
        if resp.status_code not in (200, 204):
            raise OpenBaoError("failed to store secret", resp.status_code)
        version: int = resp.json()["data"]["version"]
        return version

    async def kv_read(self, path: str, version: int | None = None) -> dict[str, str]:
        params = f"?version={version}" if version else ""
        resp = await self._request("GET", f"/v1/{self._kv}/data/{path}{params}")
        if resp.status_code == 404:
            raise OpenBaoError("secret not found", 404)
        if resp.status_code != 200:
            raise OpenBaoError("failed to read secret", resp.status_code)
        data: dict[str, str] = resp.json()["data"]["data"]
        return data

    async def kv_destroy_all(self, path: str) -> None:
        """Delete all versions and metadata for ``path``."""
        resp = await self._request("DELETE", f"/v1/{self._kv}/metadata/{path}")
        if resp.status_code not in (200, 204, 404):
            raise OpenBaoError("failed to delete secret", resp.status_code)
