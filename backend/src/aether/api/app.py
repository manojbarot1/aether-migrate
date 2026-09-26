from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from aether.api.routers import audit, connections, inventory, system, workspaces
from aether.auth.oidc import JwksVerifier
from aether.config import Settings, get_settings
from aether.core.errors import AetherError
from aether.db.session import dispose_engine, init_engine
from aether.logging import configure_logging, get_logger
from aether.secrets.openbao import OpenBaoClient
from aether.telemetry import setup_tracing
from aether.temporal_client import connect as temporal_connect

log = get_logger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        app.state.verifier = JwksVerifier(settings)
        try:
            app.state.bao = OpenBaoClient.from_settings(settings)
        except RuntimeError:
            log.warning("openbao.not_configured")
            app.state.bao = None
        try:
            app.state.temporal = await temporal_connect(settings)
        except Exception as e:  # startup must not fail if Temporal is briefly unavailable
            log.warning("temporal.unavailable", error=type(e).__name__)
            app.state.temporal = None
        log.info("api.started", version=settings.version, env=settings.env)
        yield
        await app.state.verifier.aclose()
        if app.state.bao:
            await app.state.bao.aclose()
        await dispose_engine()

    app = FastAPI(
        title="AETHER MIGRATE API",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.env != "prod" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.env != "prod" else None,
    )
    app.state.settings = settings
    app.state.bao = None
    app.state.temporal = None

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = rid
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AetherError)
    async def aether_error(request: Request, exc: AetherError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("request.error", code=exc.code, error=exc.public_message, path=request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.public_message,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError | ValidationError
    ) -> JSONResponse:
        # Strip `input` from validation errors: it may echo submitted secrets.
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "request validation failed",
                    "details": errors,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("request.unhandled", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "internal error",
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    app.include_router(system.router)
    app.include_router(workspaces.router)
    app.include_router(connections.router)
    app.include_router(inventory.router)
    app.include_router(audit.router)

    setup_tracing(settings, app)
    return app
