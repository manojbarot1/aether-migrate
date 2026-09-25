"""Shared FastAPI dependencies for AETHER MIGRATE API."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import structlog
from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser, get_current_user, require_role

log = structlog.get_logger(__name__)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session via the engine pool."""
    from db.engine import get_session

    async with get_session() as session:
        yield session


def get_workspace_id(
    user: AuthenticatedUser = Depends(get_current_user),
    workspace_id: uuid.UUID | None = Query(
        default=None,
        description="Admin override: query a specific workspace. Requires admin role.",
    ),
) -> uuid.UUID:
    """Resolve the effective workspace_id for the request.

    Regular users always operate on their own workspace from the JWT.
    Admins may pass ``?workspace_id=...`` to target another workspace.
    """
    if workspace_id is not None:
        if not user.has_role("admin"):
            raise HTTPException(status_code=403, detail="workspace_id override requires admin role")
        return workspace_id
    return user.workspace_id


__all__ = [
    "get_db_session",
    "get_workspace_id",
    "get_current_user",
    "require_role",
    "AuthenticatedUser",
]
