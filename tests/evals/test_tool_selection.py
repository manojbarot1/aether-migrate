"""Eval suite — tool selection golden prompts.

15 golden prompts verifying that the orchestrator selects the correct tools
for a given user message. Each test drives the model with a mock LLM that
returns a pre-canned tool-call decision and checks which tool was requested.

Tests are marked ``eval`` and skipped unless the ``EVAL_MODEL_TIER``
environment variable is set. This prevents them from running in standard CI
which does not have live LLM credentials.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.eval


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers", "eval: marks tests as LLM evaluation tests (skipped without EVAL_MODEL_TIER)"
    )


def _skip_unless_eval() -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not os.environ.get("EVAL_MODEL_TIER"),
        reason="Set EVAL_MODEL_TIER env var to run eval tests",
    )


# ---------------------------------------------------------------------------
# Golden prompts
# ---------------------------------------------------------------------------

GOLDEN_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "gp-01",
        "input": "Find all my AWS VMs.",
        "expected_tools": ["inventory.search_vms"],
        "expected_no_tools": ["assessment.run", "plan.create"],
    },
    {
        "id": "gp-02",
        "input": "Show VMs larger than 8 vCPU and 32 GB of memory.",
        "expected_tools": ["inventory.search_vms"],
        "expected_params": {"min_vcpu": 8, "min_memory_gib": 32},
    },
    {
        "id": "gp-03",
        "input": "Is the inventory fresh? When was the last discovery?",
        "expected_tools": ["discovery.status"],
        "expected_no_tools": ["inventory.search_vms"],
    },
    {
        "id": "gp-04",
        "input": "List all connections in my workspace.",
        "expected_tools": ["connections.list"],
        "expected_no_tools": ["inventory.search_vms"],
    },
    {
        "id": "gp-05",
        "input": "Start a new discovery run for connection abc-123.",
        "expected_tools": ["discovery.refresh"],
        "expected_params": {"connection_id": "abc-123"},
    },
    {
        "id": "gp-06",
        "input": "What changed between snapshot snap-a and snap-b?",
        "expected_tools": ["inventory.diff_snapshots"],
        "expected_no_tools": ["assessment.run"],
    },
    {
        "id": "gp-07",
        "input": "Show me the network topology around resource res-xyz.",
        "expected_tools": ["topology.get"],
        "expected_no_tools": ["inventory.search_vms"],
    },
    {
        "id": "gp-08",
        "input": "Get full details for resource id 550e8400-e29b-41d4-a716-446655440000.",
        "expected_tools": ["inventory.get_resource"],
        "expected_params": {"resource_id": "550e8400-e29b-41d4-a716-446655440000"},
    },
    {
        "id": "gp-09",
        "input": "How many stopped VMs do I have in us-east-1?",
        "expected_tools": ["inventory.search_vms"],
        "expected_params": {"region": "us-east-1", "status": "stopped"},
    },
    {
        "id": "gp-10",
        "input": "What's the sizing recommendation for migrating vm-123 to Azure?",
        "expected_tools": ["sizing.recommend"],
        "expected_no_tools": ["inventory.search_vms"],
    },
    {
        "id": "gp-11",
        "input": "Compare costs between my current AWS setup and GCP.",
        "expected_tools": ["cost.compare"],
        "expected_no_tools": ["assessment.run"],
    },
    {
        "id": "gp-12",
        "input": "Run a migration readiness assessment.",
        "expected_tools": ["assessment.run"],
        "expected_no_tools": ["plan.create"],
    },
    {
        "id": "gp-13",
        "input": "How many VMs do I have total across all providers?",
        "expected_tools": ["inventory.search_vms"],
    },
    {
        "id": "gp-14",
        "input": "Show me all running GCP instances in europe-west1.",
        "expected_tools": ["inventory.search_vms"],
        "expected_params": {"provider": "gcp", "region": "europe-west1", "status": "running"},
    },
    {
        "id": "gp-15",
        "input": "What connections does my workspace have?",
        "expected_tools": ["connections.list"],
        "expected_no_tools": ["inventory.search_vms", "discovery.status"],
    },
]


# ---------------------------------------------------------------------------
# Mock LLM that returns the expected first tool call
# ---------------------------------------------------------------------------


def _make_mock_gateway(expected_tool: str, expected_params: dict[str, Any]) -> Any:
    """Create a ModelGateway that immediately yields the expected tool call."""
    from ai.gateway import ChatChunk

    async def _fake_stream(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield ChatChunk(
            type="tool_call",
            tool_name=expected_tool,
            tool_input=expected_params,
        )
        yield ChatChunk(
            type="text",
            content=f"Tool {expected_tool} called.",
        )

    mock = MagicMock()
    mock.chat_stream = MagicMock(side_effect=_fake_stream)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_unless_eval()
@pytest.mark.parametrize("prompt", GOLDEN_PROMPTS, ids=[p["id"] for p in GOLDEN_PROMPTS])
@pytest.mark.asyncio
async def test_tool_selection(prompt: dict[str, Any]) -> None:
    """Assert the model selects the expected tool(s) for each golden prompt."""
    from ai.orchestrator import AIOrchestrator, WorkspaceAIConfig
    from tools.loader import load_all_tools
    from tools.registry import CurrentUser, ToolRegistry

    reg = ToolRegistry()
    load_all_tools(reg)

    expected_tool = prompt["expected_tools"][0]
    expected_params = prompt.get("expected_params", {})
    expected_no_tools: list[str] = prompt.get("expected_no_tools", [])

    mock_gateway = _make_mock_gateway(expected_tool, expected_params)

    # DB session factory that returns a no-op session
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None, scalars=lambda: MagicMock(all=lambda: [])))
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.delete = AsyncMock()
    factory = MagicMock(return_value=mock_db)

    orchestrator = AIOrchestrator(mock_gateway, reg, factory)
    user = CurrentUser(
        user_id="u-1",
        email="test@example.com",
        role="analyst",
        workspace_id="ws-1",
    )
    config = WorkspaceAIConfig(workspace_id="ws-1")

    called_tools: list[str] = []
    async for chunk in await orchestrator.run(None, prompt["input"], user, config):
        if chunk.type == "tool_call":
            called_tools.append(chunk.tool_name or "")

    for expected in prompt["expected_tools"]:
        assert expected in called_tools, (
            f"[{prompt['id']}] Expected tool '{expected}' not called. Called: {called_tools}"
        )

    for forbidden in expected_no_tools:
        assert forbidden not in called_tools, (
            f"[{prompt['id']}] Forbidden tool '{forbidden}' was called"
        )

    if expected_params:
        mock_gateway.chat_stream.assert_called()
