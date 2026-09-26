"""Authenticated principal and role checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.audit.writer import Actor
from aether.auth.oidc import TokenClaims
from aether.core.enums import ActorType, Role
from aether.core.errors import ForbiddenError
from aether.db.models import User


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    subject: str
    email: str | None
    display_name: str | None
    is_platform_admin: bool
    roles: dict[uuid.UUID, Role] = field(default_factory=dict)  # workspace_id -> role

    @property
    def actor(self) -> Actor:
        return Actor(ActorType.USER, str(self.user_id), self.email or self.display_name or self.subject)

    def role_in(self, workspace_id: uuid.UUID) -> Role | None:
        role = self.roles.get(workspace_id)
        # Platform admins act as workspace admins everywhere.
        if self.is_platform_admin:
            return Role.ADMIN
        return role

    def require(self, workspace_id: uuid.UUID, minimum: Role) -> Role:
        role = self.role_in(workspace_id)
        if role is None:
            # Same error as a missing workspace so membership isn't leaked.
            raise ForbiddenError("workspace not found or access denied")
        if not role.at_least(minimum):
            raise ForbiddenError(f"requires role '{minimum.value}' or higher")
        return role

    def require_platform_admin(self) -> None:
        if not self.is_platform_admin:
            raise ForbiddenError("requires platform administrator")


async def upsert_user(session: AsyncSession, claims: TokenClaims, admin_role: str) -> User:
    """Just-in-time provisioning: create or refresh the local user from token claims.

    Platform-admin status is derived from the IdP realm role on every request, so
    revoking the role in the IdP takes effect at the next token refresh.
    """
    user = (await session.execute(select(User).where(User.subject == claims.subject))).scalar_one_or_none()
    is_admin = admin_role in claims.realm_roles
    now = datetime.now(UTC)
    if user is None:
        user = User(
            id=uuid.uuid4(),
            subject=claims.subject,
            email=claims.email,
            display_name=claims.name,
            is_platform_admin=is_admin,
            last_login_at=now,
        )
        session.add(user)
        await session.flush()
        await session.refresh(user, attribute_names=["memberships"])
    else:
        user.email = claims.email
        user.display_name = claims.name
        user.is_platform_admin = is_admin
        # Only bump last_login_at at most once per minute to avoid write amplification.
        if user.last_login_at is None or (now - user.last_login_at).total_seconds() > 60:
            user.last_login_at = now
    return user


def principal_from_user(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        subject=user.subject,
        email=user.email,
        display_name=user.display_name,
        is_platform_admin=user.is_platform_admin,
        roles={m.workspace_id: Role(m.role) for m in user.memberships},
    )
