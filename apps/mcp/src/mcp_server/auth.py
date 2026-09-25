"""MCP server JWT validation — reuses the same logic as the API app.

Validates a Bearer token (JWT or aet_ API token) and returns a CurrentUser
compatible with the ToolRegistry.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import HTTPException, status
from tools.registry import CurrentUser

log = structlog.get_logger(__name__)


async def validate_token(token: str) -> CurrentUser:
    """Validate a Bearer token and return a CurrentUser.

    Supports:
    - OIDC JWT (validated against OIDC_JWKS_URL)
    - API token (prefixed with ``aet_``, validated via DB lookup)
    """
    if token.startswith("aet_"):
        return await _validate_api_token(token)
    return await _validate_jwt(token)


async def _validate_jwt(token: str) -> CurrentUser:
    from jose import JWTError, jwt

    jwks_url = os.environ.get("OIDC_JWKS_URL", "")
    if not jwks_url:
        raise HTTPException(status_code=503, detail="OIDC not configured")

    import urllib.request

    with urllib.request.urlopen(jwks_url, timeout=5) as resp:  # noqa: S310
        jwks = resp.read().decode()

    issuer = os.environ.get("OIDC_ISSUER", "")
    audience = os.environ.get("OIDC_CLIENT_ID", "aether-api")

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except JWTError as exc:
        log.warning("mcp_jwt_validation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    roles: list[str] = claims.get("realm_access", {}).get("roles", [])
    workspace_id_raw = claims.get("workspace_id", "")
    try:
        workspace_id = str(uuid.UUID(workspace_id_raw))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Missing workspace_id claim")

    # Use highest role for tool registry (takes first matching role)
    _ROLE_ORDER = ["viewer", "analyst", "connection-admin", "approver", "operator", "admin"]
    effective_role = "viewer"
    for role in reversed(_ROLE_ORDER):
        if role in roles:
            effective_role = role
            break

    return CurrentUser(
        user_id=claims["sub"],
        email=claims.get("email", ""),
        role=effective_role,
        workspace_id=workspace_id,
    )


async def _validate_api_token(token: str) -> CurrentUser:
    from db.engine import get_session
    from db.models import ApiTokenRow, UserRow
    from sqlalchemy import select

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    async with get_session() as session:
        result = await session.execute(
            select(ApiTokenRow).where(ApiTokenRow.token_hash == token_hash)
        )
        row: ApiTokenRow | None = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API token")
    if row.revoked_at is not None:
        raise HTTPException(status_code=401, detail="API token has been revoked")
    if row.expires_at is not None and row.expires_at < datetime.now(tz=UTC):
        raise HTTPException(status_code=401, detail="API token has expired")

    async with get_session() as session:
        user_result = await session.execute(
            select(UserRow).where(UserRow.id == row.user_id)
        )
        user: UserRow | None = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="API token user not found")

    return CurrentUser(
        user_id=str(row.user_id),
        email=user.email,
        role=user.role,
        workspace_id=str(row.workspace_id),
    )
