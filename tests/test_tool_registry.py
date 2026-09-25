"""Unit tests for ToolRegistry.

Coverage:
- viewer role cannot access analyst-only tools
- mutating tools are never returned for any role
- tool execution validates role before calling handler
- unknown tool raises appropriate error
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from core.errors import AuthorizationError
from pydantic import BaseModel
from tools.registry import CurrentUser, ToolDefinition, ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class DummyInput(BaseModel):
    value: str


class DummyOutput(BaseModel):
    result: str


def _make_tool(
    name: str,
    side_effect_class: str = "read",
    required_role: str = "analyst",
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool {name}",
        input_schema=DummyInput,
        output_schema=DummyOutput,
        side_effect_class=side_effect_class,  # type: ignore[arg-type]
        required_role=required_role,
    )


async def _dummy_handler(
    input_data: dict,
    user: CurrentUser,
    db: MagicMock,
) -> dict:
    return {"result": "ok"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_viewer_cannot_access_analyst_tool() -> None:
    """viewer role should not see tools that require analyst."""
    reg = ToolRegistry()
    reg.register(_make_tool("analyst.tool", required_role="analyst"))

    viewer_tools = reg.list_for_role("viewer")
    assert not any(t.name == "analyst.tool" for t in viewer_tools)


def test_analyst_can_access_viewer_tool() -> None:
    """analyst can access tools marked as viewer (higher role includes lower)."""
    reg = ToolRegistry()
    reg.register(_make_tool("viewer.tool", required_role="viewer"))

    analyst_tools = reg.list_for_role("analyst")
    assert any(t.name == "viewer.tool" for t in analyst_tools)


def test_mutating_tools_never_returned_for_any_role() -> None:
    """Mutating tools must never appear in list_for_role regardless of role."""
    reg = ToolRegistry()
    reg.register(_make_tool("dangerous.mutate", side_effect_class="mutate", required_role="viewer"))

    for role in ("viewer", "analyst", "connection-admin", "approver", "operator", "admin"):
        tools = reg.list_for_role(role)
        assert not any(t.name == "dangerous.mutate" for t in tools), (
            f"Mutating tool exposed to role '{role}'"
        )


def test_role_hierarchy_ordering() -> None:
    """Roles inherit access to all lower roles' tools."""
    reg = ToolRegistry()
    reg.register(_make_tool("r.viewer", required_role="viewer"))
    reg.register(_make_tool("r.analyst", required_role="analyst"))
    reg.register(_make_tool("r.connection-admin", required_role="connection-admin"))
    reg.register(_make_tool("r.operator", required_role="operator"))

    admin_tools = {t.name for t in reg.list_for_role("admin")}
    assert "r.viewer" in admin_tools
    assert "r.analyst" in admin_tools
    assert "r.connection-admin" in admin_tools
    assert "r.operator" in admin_tools

    viewer_tools = {t.name for t in reg.list_for_role("viewer")}
    assert "r.viewer" in viewer_tools
    assert "r.analyst" not in viewer_tools
    assert "r.operator" not in viewer_tools


@pytest.mark.asyncio
async def test_execute_validates_role() -> None:
    """execute() must raise AuthorizationError if user lacks required role."""
    reg = ToolRegistry()
    reg.register(_make_tool("restricted.tool", required_role="operator"))
    reg.register_handler("restricted.tool", _dummy_handler)

    user = CurrentUser(
        user_id="u-1", email="user@test.com", role="viewer", workspace_id="ws-1"
    )
    mock_db = MagicMock()

    with pytest.raises(AuthorizationError):
        await reg.execute("restricted.tool", {"value": "x"}, user, mock_db)


@pytest.mark.asyncio
async def test_execute_mutating_tool_raises() -> None:
    """execute() must raise AuthorizationError for mutating tools regardless of role."""
    reg = ToolRegistry()
    reg.register(_make_tool("bad.mutate", side_effect_class="mutate", required_role="viewer"))
    reg.register_handler("bad.mutate", _dummy_handler)

    admin = CurrentUser(
        user_id="u-admin", email="admin@test.com", role="admin", workspace_id="ws-1"
    )
    mock_db = MagicMock()

    with pytest.raises(AuthorizationError):
        await reg.execute("bad.mutate", {}, admin, mock_db)


@pytest.mark.asyncio
async def test_execute_unknown_tool_raises_key_error() -> None:
    """execute() must raise KeyError for unregistered tools."""
    reg = ToolRegistry()
    user = CurrentUser(
        user_id="u-1", email="u@t.com", role="admin", workspace_id="ws-1"
    )
    mock_db = MagicMock()

    with pytest.raises(KeyError, match="unknown.tool"):
        await reg.execute("unknown.tool", {}, user, mock_db)


@pytest.mark.asyncio
async def test_execute_calls_handler_when_authorized() -> None:
    """execute() calls the handler and returns its result when authorized."""
    reg = ToolRegistry()
    reg.register(_make_tool("ok.tool", required_role="viewer"))
    called_with: list[dict] = []

    async def _handler(input_data: dict, user: CurrentUser, db: MagicMock) -> dict:
        called_with.append(input_data)
        return {"result": "handled"}

    reg.register_handler("ok.tool", _handler)

    user = CurrentUser(
        user_id="u-1", email="u@t.com", role="viewer", workspace_id="ws-1"
    )
    mock_db = MagicMock()
    result = await reg.execute("ok.tool", {"value": "test"}, user, mock_db)

    assert result == {"result": "handled"}
    assert called_with == [{"value": "test"}]


def test_register_duplicate_raises() -> None:
    """Registering two tools with the same name raises ValueError."""
    reg = ToolRegistry()
    tool = _make_tool("dup.tool")
    reg.register(tool)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(tool)


def test_get_returns_none_for_missing() -> None:
    """get() returns None for unknown tools."""
    reg = ToolRegistry()
    assert reg.get("nonexistent") is None
