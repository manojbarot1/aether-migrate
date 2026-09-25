"""FastAPI application entry point for AETHER MIGRATE API.

Startup order:
  1. Configure structured logging (structlog)
  2. Configure OpenTelemetry
  3. Create FastAPI app with middleware
  4. Register routers
  5. Register error handlers
"""

from __future__ import annotations

import os

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from api.routers import (
    assessment,
    audit,
    connections,
    cost,
    discovery,
    inventory,
    plans,
    snapshots,
    topology,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------

_otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if _otel_endpoint:
    _resource = Resource.create({"service.name": "aether-api"})
    _provider = TracerProvider(resource=_resource)
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint)))
    trace.set_tracer_provider(_provider)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AETHER MIGRATE API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS — allowed origins read from environment (comma-separated)
_cors_origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

FastAPIInstrumentor.instrument_app(app)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(connections.router, prefix="/api/v1")
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(snapshots.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(discovery.router, prefix="/api/v1")
app.include_router(topology.router, prefix="/api/v1")
app.include_router(cost.router, prefix="/api/v1")
app.include_router(assessment.router, prefix="/api/v1")
app.include_router(plans.router, prefix="/api/v1")

# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/livez", include_in_schema=False)
@app.get("/api/v1/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    """Liveness probe — returns 200 if the process is alive."""
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
@app.get("/api/v1/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    """Readiness probe — checks DB connectivity."""
    try:
        from db.engine import get_session
        from sqlalchemy import text

        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok"})
    except Exception:
        log.warning("readyz: db not ready")
        return JSONResponse({"status": "unavailable"}, status_code=503)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log internally but never expose stack trace to clients
    log.error("unhandled_exception", path=str(request.url.path), exc_info=exc)
    return JSONResponse({"error": "internal_error"}, status_code=500)


# ---------------------------------------------------------------------------
# Startup / shutdown lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    log.info(
        "aether_api_started",
        version="0.1.0",
        cors_origins=_cors_origins,
        otel_endpoint=_otel_endpoint or "disabled",
    )
