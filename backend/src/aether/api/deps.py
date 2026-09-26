from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from aether.auth.oidc import JwksVerifier, TokenError
from aether.auth.principal import Principal, principal_from_user, upsert_user
from aether.config import Settings
from aether.core.enums import Role
from aether.core.errors import AetherError
from aether.db.session import session_scope, set_workspace
from aether.secrets.openbao import OpenBaoClient


class UnauthorizedError(AetherError):
    status_code = 401
    code = "unauthorized"


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_verifier(request: Request) -> JwksVerifier:
    verifier: JwksVerifier = request.app.state.verifier
    return verifier


def get_bao(request: Request) -> OpenBaoClient:
    bao: OpenBaoClient | None = request.app.state.bao
    if bao is None:
        raise AetherError("secret store is not configured")
    return bao


def get_temporal(request: Request) -> Client:
    client: Client | None = request.app.state.temporal
    if client is None:
        raise AetherError("workflow engine is unavailable")
    return client


def request_id(request: Request) -> str | None:
    rid: str | None = getattr(request.state, "request_id", None)
    return rid


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


# scope="function": commit happens before the response is sent, so a 2xx always
# means the change (and its audit record) is durable.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    verifier: Annotated[JwksVerifier, Depends(get_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("missing bearer token")
    try:
        claims = await verifier.verify(authorization[7:].strip())
    except TokenError as e:
        raise UnauthorizedError("invalid or expired token") from e
    user = await upsert_user(session, claims, settings.oidc_admin_role)
    return principal_from_user(user)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    workspace_id: uuid.UUID
    principal: Principal
    role: Role
    session: AsyncSession


def require_role(minimum: Role):  # type: ignore[no-untyped-def]
    """Dependency factory: resolves ``{workspace_id}`` from the path, checks the
    caller's role and scopes the session to the workspace for RLS."""

    async def dep(workspace_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> WorkspaceContext:
        role = principal.require(workspace_id, minimum)
        await set_workspace(session, workspace_id)
        return WorkspaceContext(workspace_id, principal, role, session)

    return dep
