"""AETHER MIGRATE API — authentication and authorisation.

Supports two authentication methods:
1. OIDC JWT (Bearer token from Keycloak) — validated against the JWKS endpoint.
2. API token (Bearer token prefixed with ``aet_``) — SHA-256 hashed and looked
   up in the ``api_tokens`` table.

The JWKS URL is read from the ``OIDC_JWKS_URL`` environment variable.
Role claims are read from the ``realm_access.roles`` JWT field (Keycloak default).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = structlog.get_logger(__name__)

_bearer = HTTPBearer(auto_error=True)

# Role hierarchy: higher index = more privilege
_ROLE_ORDER = ["viewer", "analyst", "connection-admin", "approver", "operator", "admin"]


def _role_gte(user_role: str, required: str) -> bool:
    """Return True if *user_role* has at least as much privilege as *required*."""
    try:
        return _ROLE_ORDER.index(user_role) >= _ROLE_ORDER.index(required)
    except ValueError:
        return False


class AuthenticatedUser:
    """Carries identity and claims for the authenticated caller."""

    def __init__(
        self,
        user_id: uuid.UUID,
        email: str,
        workspace_id: uuid.UUID,
        roles: list[str],
        token_kind: str,  # "jwt" | "api_token"
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.workspace_id = workspace_id
        self.roles = roles
        self.token_kind = token_kind

    def has_role(self, required_role: str) -> bool:
        return any(_role_gte(r, required_role) for r in self.roles)


async def _validate_jwt(token: str) -> AuthenticatedUser:
    """Validate a JWT against the OIDC JWKS endpoint."""
    from jose import JWTError, jwt

    jwks_url = os.environ.get("OIDC_JWKS_URL", "")
    if not jwks_url:
        raise HTTPException(status_code=503, detail="OIDC not configured")

    # Fetch JWKS (in production use a cached/async HTTP client)
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
        log.warning("jwt_validation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    roles: list[str] = (
        claims.get("realm_access", {}).get("roles", [])
    )
    workspace_id_raw = claims.get("workspace_id", "")
    try:
        workspace_id = uuid.UUID(workspace_id_raw)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Missing workspace_id claim")

    return AuthenticatedUser(
        user_id=uuid.UUID(claims["sub"]),
        email=claims.get("email", ""),
        workspace_id=workspace_id,
        roles=roles,
        token_kind="jwt",
    )


async def _validate_api_token(token: str) -> AuthenticatedUser:
    """Validate an API token by SHA-256 hash lookup."""
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

    # Fetch the associated user for role information
    async with get_session() as session:
        user_result = await session.execute(
            select(UserRow).where(UserRow.id == row.user_id)
        )
        user: UserRow | None = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="API token user not found")

    return AuthenticatedUser(
        user_id=row.user_id,
        email=user.email,
        workspace_id=row.workspace_id,
        roles=[user.role],
        token_kind="api_token",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> AuthenticatedUser:
    """FastAPI dependency — validates the Bearer token and returns the caller."""
    token = credentials.credentials

    # API tokens start with "aet_"
    if token.startswith("aet_"):
        return await _validate_api_token(token)

    return await _validate_jwt(token)


def require_role(required_role: str):  # noqa: ANN201
    """Dependency factory that enforces a minimum RBAC role.

    Usage::

        @router.post("/connections", dependencies=[Depends(require_role("connection-admin"))])
    """
    async def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not user.has_role(required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role '{required_role}' or higher",
            )
        return user

    return _check
