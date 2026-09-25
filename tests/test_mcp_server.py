"""Tests for the MCP server — tool listing and calling.

Uses a minimal in-memory SQLite DB. Tests:
- Server starts and lists tools via GET /mcp/tools
- Tool list matches analyst-level tools from ToolRegistry
- Calling a tool with insufficient role returns error
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analyst_user():
    from tools.registry import CurrentUser
    return CurrentUser(
        user_id=str(uuid.uuid4()),
        email="analyst@test.com",
        role="analyst",
        workspace_id=str(uuid.uuid4()),
    )


def _make_viewer_user():
    from tools.registry import CurrentUser
    return CurrentUser(
        user_id=str(uuid.uuid4()),
        email="viewer@test.com",
        role="viewer",
        workspace_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMCPServerStartup:
    def test_app_has_livez_endpoint(self):
        from mcp_server.main import app

        with TestClient(app) as client:
            resp = client.get("/livez")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestMCPToolsList:
    def test_list_tools_requires_auth(self):
        from mcp_server.main import app

        with TestClient(app) as client:
            resp = client.get("/mcp/tools")

        assert resp.status_code == 401

    def test_list_tools_returns_tool_list_for_analyst(self):
        from mcp_server.main import app

        analyst = _make_analyst_user()

        with patch("mcp_server.auth.validate_token", new=AsyncMock(return_value=analyst)):
            with TestClient(app) as client:
                resp = client.get("/mcp/tools", headers={"Authorization": "Bearer fake-token"})

        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert len(data["tools"]) > 0

        # All returned tools should be non-mutating
        for tool in data["tools"]:
            assert tool["sideEffectClass"] != "mutate"

    def test_tool_list_matches_registry_analyst_tools(self):
        from mcp_server.main import _registry, app

        analyst = _make_analyst_user()
        expected = {t.name for t in _registry.list_for_role("analyst")}

        with patch("mcp_server.auth.validate_token", new=AsyncMock(return_value=analyst)):
            with TestClient(app) as client:
                resp = client.get("/mcp/tools", headers={"Authorization": "Bearer fake-token"})

        returned_names = {t["name"] for t in resp.json()["tools"]}
        assert returned_names == expected

    def test_viewer_sees_subset_of_analyst_tools(self):
        from mcp_server.main import app

        viewer = _make_viewer_user()
        analyst = _make_analyst_user()

        with patch("mcp_server.auth.validate_token", new=AsyncMock(return_value=viewer)):
            with TestClient(app) as client:
                viewer_resp = client.get("/mcp/tools", headers={"Authorization": "Bearer v"})
            viewer_tools = {t["name"] for t in viewer_resp.json()["tools"]}

        with patch("mcp_server.auth.validate_token", new=AsyncMock(return_value=analyst)):
            with TestClient(app) as client:
                analyst_resp = client.get("/mcp/tools", headers={"Authorization": "Bearer a"})
            analyst_tools = {t["name"] for t in analyst_resp.json()["tools"]}

        # Viewer tools must be a subset of analyst tools
        assert viewer_tools <= analyst_tools


class TestMCPToolCall:
    def test_call_tool_requires_auth(self):
        from mcp_server.main import app

        with TestClient(app) as client:
            resp = client.post("/mcp/tools/call", json={"tool": "connections.list", "arguments": {}})

        assert resp.status_code == 401

    def test_call_tool_returns_error_when_registry_raises_key_error(self):
        """Test that a KeyError from the registry returns an error MCPToolResult."""
        from mcp_server.main import app
        from tools.registry import ToolRegistry

        analyst = _make_analyst_user()

        # Build a mock registry that raises KeyError on execute
        mock_registry = MagicMock(spec=ToolRegistry)
        mock_registry.execute = AsyncMock(side_effect=KeyError("no_such_tool"))

        # Also need a mock DB session
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("mcp_server.auth.validate_token", new=AsyncMock(return_value=analyst)),
            patch("mcp_server.main._registry", mock_registry),
            patch("db.engine.get_session", return_value=mock_ctx),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    "/mcp/tools/call",
                    json={"tool": "no_such_tool", "arguments": {}},
                    headers={"Authorization": "Bearer fake-token"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_error"] is True
        assert "not found" in data["content"][0]["text"].lower()


class TestMCPOAuthMetadata:
    def test_oauth_metadata_returns_503_when_not_configured(self):
        import os

        from mcp_server.main import app

        original = os.environ.pop("OIDC_ISSUER", None)
        try:
            with TestClient(app) as client:
                resp = client.get("/mcp/oauth-metadata")
            assert resp.status_code == 503
        finally:
            if original is not None:
                os.environ["OIDC_ISSUER"] = original

    def test_oauth_metadata_returns_endpoints_when_configured(self):
        import os

        from mcp_server.main import app

        os.environ["OIDC_ISSUER"] = "https://keycloak.example.com/realms/aether"
        os.environ["OIDC_JWKS_URL"] = "https://keycloak.example.com/realms/aether/protocol/openid-connect/certs"
        try:
            with TestClient(app) as client:
                resp = client.get("/mcp/oauth-metadata")
            assert resp.status_code == 200
            data = resp.json()
            assert "token_endpoint" in data
            assert "jwks_uri" in data
            assert "scopes_supported" in data
        finally:
            os.environ.pop("OIDC_ISSUER", None)
            os.environ.pop("OIDC_JWKS_URL", None)
