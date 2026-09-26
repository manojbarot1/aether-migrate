"""The `assistant` service: chat orchestration with LLM egress.

It runs separately from the main API so the only process that can reach a model
provider holds no secret-store credentials and has no route to the cloud connector.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from aether.api.app import install_common
from aether.api.routers import system
from aether.assistant import api as assistant_api
from aether.assistant.orchestrator import TurnDeps
from aether.auth.oidc import JwksVerifier
from aether.config import Settings, get_settings
from aether.db.session import dispose_engine, init_engine, session_scope
from aether.logging import configure_logging, get_logger
from aether.telemetry import setup_tracing
from aether.temporal_client import connect as temporal_connect

log = get_logger(__name__)

RETENTION_INTERVAL_S = 6 * 3600


async def purge_old_conversations(days: int) -> int:
    async with session_scope() as s:
        n = (
            await s.execute(text("SELECT aether_purge_conversations(make_interval(days => :d))"), {"d": days})
        ).scalar_one()
    return int(n)


async def _retention_loop(settings: Settings) -> None:
    while True:
        try:
            n = await purge_old_conversations(settings.assistant_retention_days)
            if n:
                log.info("assistant.retention.purged", conversations=n)
        except Exception as e:
            log.warning("assistant.retention.failed", error=type(e).__name__)
        await asyncio.sleep(RETENTION_INTERVAL_S)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        app.state.verifier = JwksVerifier(settings)
        try:
            app.state.temporal = await temporal_connect(settings)
        except Exception as e:
            log.warning("temporal.unavailable", error=type(e).__name__)
            app.state.temporal = None
        app.state.turn_deps = TurnDeps(settings=settings, temporal=app.state.temporal)
        retention = asyncio.create_task(_retention_loop(settings))
        log.info("assistant.started", version=settings.version, provider=settings.assistant_provider)
        yield
        retention.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retention
        pending = list(app.state.turn_tasks)
        if pending:  # let in-flight turns finish their current step and release their locks
            await asyncio.wait(pending, timeout=20)
        await app.state.verifier.aclose()
        await dispose_engine()

    app = FastAPI(
        title="AETHER MIGRATE assistant",
        version=settings.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.bao = None
    app.state.temporal = None
    app.state.turn_tasks = set()
    install_common(app)
    app.include_router(system.router)
    app.include_router(assistant_api.router)
    setup_tracing(settings, app)
    return app
