"""Integration fixtures: real Postgres (compose test stack), fake IdP/OpenBao/Temporal."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from aether.api.app import create_app
from aether.auth.oidc import TokenClaims, TokenError
from aether.config import Settings
from aether.db import session as db_session
from aether.secrets.openbao import OpenBaoError

pytestmark = pytest.mark.integration

if not os.environ.get("AETHER_DB_HOST"):
    pytest.skip("integration tests need the compose test stack", allow_module_level=True)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(env="test", log_json=False, oidc_issuer="https://aether.test/auth/realms/aether")


@pytest.fixture(scope="session")
async def owner_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    owner = settings.model_copy(update={"db_user": "aether_owner", "db_password_file": "pg_owner_password"})
    engine = create_async_engine(owner.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def app_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = db_session.init_engine(settings)
    yield engine
    await db_session.dispose_engine()


# ---------------------------------------------------------------------------- fakes


class FakeVerifier:
    """Accepts tokens of the form ``sub|email|role1,role2``."""

    async def verify(self, token: str) -> TokenClaims:
        try:
            sub, email, roles = token.split("|")
        except ValueError:
            raise TokenError("bad fake token") from None
        return TokenClaims(sub, email, email.split("@")[0], frozenset(r for r in roles.split(",") if r), {})

    async def aclose(self) -> None:
        pass


@dataclass
class FakeBao:
    store: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    async def kv_write(self, path: str, data: dict[str, str]) -> int:
        self.store.setdefault(path, []).append(dict(data))
        return len(self.store[path])

    async def kv_read(self, path: str, version: int | None = None) -> dict[str, str]:
        if path not in self.store:
            raise OpenBaoError("secret not found", 404)
        versions = self.store[path]
        return versions[(version or len(versions)) - 1]

    async def kv_destroy_all(self, path: str) -> None:
        self.store.pop(path, None)

    async def health(self) -> dict[str, Any]:
        return {"sealed": False}

    async def aclose(self) -> None:
        pass


@pytest.fixture
def bao() -> FakeBao:
    return FakeBao()


@pytest.fixture
async def app(settings: Settings, bao: FakeBao) -> FastAPI:
    application = create_app(settings)
    application.state.verifier = FakeVerifier()
    application.state.bao = bao
    application.state.temporal = None
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth(sub: str, email: str | None = None, roles: str = "") -> dict[str, str]:
    return {"Authorization": f"Bearer {sub}|{email or sub + '@example.com'}|{roles}"}


ADMIN = auth("admin-" + uuid.uuid4().hex[:6], roles="platform-admin")


@pytest.fixture
async def workspace(client: httpx.AsyncClient) -> dict[str, Any]:
    slug = "ws-" + uuid.uuid4().hex[:8]
    r = await client.post("/api/v1/workspaces", json={"slug": slug, "name": slug}, headers=ADMIN)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


async def make_member(client: httpx.AsyncClient, ws_id: str, role: str) -> dict[str, str]:
    """Sign a new user in (JIT provisioning) and grant them ``role`` in the workspace."""
    sub = f"{role}-{uuid.uuid4().hex[:6]}"
    headers = auth(sub)
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200
    r = await client.put(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": f"{sub}@example.com", "role": role},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    return headers


async def exec_sql(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.begin() as conn:
        return await conn.execute(text(sql), params)
