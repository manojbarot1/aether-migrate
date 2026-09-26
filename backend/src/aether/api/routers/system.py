from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from aether.api.deps import SettingsDep
from aether.api.schemas import ClientConfig
from aether.db.session import session_scope

router = APIRouter()


@router.get("/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    return {"status": "ok"}


async def _check_db() -> None:
    async with session_scope() as s:
        await s.execute(text("SELECT 1"))


async def _check_temporal(request: Request) -> None:
    client = request.app.state.temporal
    if client is None:
        raise RuntimeError("not connected")
    await client.service_client.check_health()


async def _check_bao(request: Request) -> None:
    bao = request.app.state.bao
    if bao is None:
        raise RuntimeError("not configured")
    h = await bao.health()
    if h.get("sealed", True):
        raise RuntimeError("sealed")


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    checks = {"database": _check_db(), "temporal": _check_temporal(request), "openbao": _check_bao(request)}
    results: dict[str, str] = {}
    for name, coro in checks.items():
        try:
            await asyncio.wait_for(coro, timeout=3)
            results[name] = "ok"
        except Exception as e:
            results[name] = f"fail: {type(e).__name__}"
    # The database is required to serve anything; Temporal/OpenBao degrade features.
    ready = results["database"] == "ok"
    response.status_code = 200 if ready else 503
    return {"ready": ready, "checks": results}


@router.get("/api/v1/meta/client-config", response_model=ClientConfig, tags=["meta"])
async def client_config(settings: SettingsDep) -> ClientConfig:
    """Public, unauthenticated: what the SPA needs to start the OIDC login."""
    return ClientConfig(
        oidc_authority=settings.oidc_issuer,
        oidc_client_id=settings.oidc_client_id,
        version=settings.version,
        env=settings.env,
    )
