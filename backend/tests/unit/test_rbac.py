import uuid

import pytest

from aether.auth.principal import Principal
from aether.core.enums import Role
from aether.core.errors import ForbiddenError


def _p(roles: dict[uuid.UUID, Role], admin: bool = False) -> Principal:
    return Principal(uuid.uuid4(), "sub", "a@example.com", "A", admin, roles)


def test_role_ordering() -> None:
    assert Role.ADMIN.at_least(Role.VIEWER)
    assert Role.CONNECTION_ADMIN.at_least(Role.ANALYST)
    assert not Role.ANALYST.at_least(Role.CONNECTION_ADMIN)
    assert Role.VIEWER.at_least(Role.VIEWER)


def test_require_enforces_minimum_role() -> None:
    ws = uuid.uuid4()
    p = _p({ws: Role.ANALYST})
    assert p.require(ws, Role.VIEWER) == Role.ANALYST
    with pytest.raises(ForbiddenError, match="connection-admin"):
        p.require(ws, Role.CONNECTION_ADMIN)


def test_non_member_gets_same_error_as_missing_workspace() -> None:
    p = _p({})
    with pytest.raises(ForbiddenError, match="not found or access denied"):
        p.require(uuid.uuid4(), Role.VIEWER)


def test_platform_admin_is_admin_everywhere() -> None:
    p = _p({}, admin=True)
    assert p.require(uuid.uuid4(), Role.ADMIN) == Role.ADMIN
    p.require_platform_admin()
    with pytest.raises(ForbiddenError):
        _p({}).require_platform_admin()
