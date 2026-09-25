"""AETHER MIGRATE MCP Server — Phase 7.

Implements the Model Context Protocol (streamable HTTP transport) using
FastAPI. Exposes all analyst-level tools from the ToolRegistry dynamically.

Authentication:
  Bearer token validated via the same OIDC / API-token path as the REST API.
  Token endpoint: {OIDC_ISSUER}/protocol/openid-connect/token
  JWKS:           {OIDC_JWKS_URL}
  Scopes:         openid profile

The MCP server uses the SAME ToolRegistry as the AI orchestrator — no tool
logic is duplicated here.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from tools.loader import load_all_tools
from tools.registry import ToolRegistry

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
# Build the tool registry at module load time
# ---------------------------------------------------------------------------

_registry = ToolRegistry()
load_all_tools(_registry)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AETHER MIGRATE MCP",
    version="0.1.0",
    description=(
        "Model Context Protocol server for AETHER MIGRATE. "
        "Exposes migration tools with OIDC authentication and full audit logging."
    ),
    docs_url="/mcp/docs",
    openapi_url="/mcp/openapi.json",
)

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# ---------------------------------------------------------------------------
# MCP protocol schemas
# ---------------------------------------------------------------------------


class MCPToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


class MCPToolResult(BaseModel):
    tool: str
    content: list[dict[str, Any]]
    is_error: bool = False


class MCPToolsListResponse(BaseModel):
    tools: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Utility: extract Bearer token
# ---------------------------------------------------------------------------


def _extract_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization[len("Bearer "):]


# ---------------------------------------------------------------------------
# GET /mcp/tools — list all analyst-level tools
# ---------------------------------------------------------------------------


@app.get("/mcp/tools", response_model=MCPToolsListResponse)
async def list_tools(
    authorization: str | None = Header(default=None),
) -> MCPToolsListResponse:
    """List all tools available to the caller's role.

    Returns tool name, description, and input schema for each tool.
    """
    from mcp_server.auth import validate_token

    token = _extract_token(authorization)
    user = await validate_token(token)

    tools = _registry.list_for_role(user.role)

    return MCPToolsListResponse(
        tools=[
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema.model_json_schema(),
                "tags": t.tags,
                "sideEffectClass": t.side_effect_class,
                "requiredRole": t.required_role,
            }
            for t in tools
        ]
    )


# ---------------------------------------------------------------------------
# POST /mcp/tools/call — invoke a tool
# ---------------------------------------------------------------------------


@app.post("/mcp/tools/call", response_model=MCPToolResult)
async def call_tool(
    body: MCPToolCall,
    request: Request,
    authorization: str | None = Header(default=None),
) -> MCPToolResult:
    """Invoke a tool by name with the given arguments.

    Authentication and RBAC are enforced. Every call is audit-logged.
    """
    from mcp_server.auth import validate_token

    token = _extract_token(authorization)
    user = await validate_token(token)

    # Get DB session
    from db.engine import get_session

    async with get_session() as session:
        try:
            result = await _registry.execute(
                name=body.tool,
                input_data=body.arguments,
                user=user,
                db=session,
            )
        except KeyError as exc:
            return MCPToolResult(
                tool=body.tool,
                content=[{"type": "text", "text": f"Tool not found: {exc}"}],
                is_error=True,
            )
        except Exception as exc:
            log.error(
                "mcp_tool_error",
                tool=body.tool,
                user=user.user_id,
                error=str(exc),
            )
            return MCPToolResult(
                tool=body.tool,
                content=[{"type": "text", "text": f"Tool execution failed: {type(exc).__name__}"}],
                is_error=True,
            )

    # Wrap result as MCP content
    import json

    content_text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    log.info("mcp_tool_called", tool=body.tool, user=user.user_id)

    return MCPToolResult(
        tool=body.tool,
        content=[{"type": "text", "text": content_text}],
        is_error=False,
    )


# ---------------------------------------------------------------------------
# GET /mcp/oauth-metadata — OAuth 2.1 authorization server metadata
# ---------------------------------------------------------------------------


@app.get("/mcp/oauth-metadata")
async def oauth_metadata() -> JSONResponse:
    """Return OAuth 2.1 server metadata for MCP clients.

    Clients use this to discover the token endpoint, JWKS, and supported scopes.
    """
    oidc_issuer = os.environ.get("OIDC_ISSUER", "")
    jwks_url = os.environ.get("OIDC_JWKS_URL", "")

    if not oidc_issuer:
        return JSONResponse(
            {"error": "OIDC not configured"},
            status_code=503,
        )

    return JSONResponse({
        "issuer": oidc_issuer,
        "token_endpoint": f"{oidc_issuer}/protocol/openid-connect/token",
        "jwks_uri": jwks_url or f"{oidc_issuer}/protocol/openid-connect/certs",
        "scopes_supported": ["openid", "profile"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    })


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    try:
        from db.engine import get_session
        from sqlalchemy import text

        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok"})
    except Exception:
        log.warning("mcp_readyz: db not ready")
        return JSONResponse({"status": "unavailable"}, status_code=503)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.exception_handler(404)
async def not_found(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


@app.exception_handler(500)
async def internal_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("mcp_unhandled_exception", path=str(request.url.path), exc_info=exc)
    return JSONResponse({"error": "internal_error"}, status_code=500)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    tool_count = len(_registry)
    log.info(
        "aether_mcp_started",
        version="0.1.0",
        tools_registered=tool_count,
        oidc_issuer=os.environ.get("OIDC_ISSUER", "not configured"),
    )
