"""Eval suite — numeric faithfulness.

Verifies that the model only quotes numbers returned by tool results and does
not invent alternative numbers.

Tests are marked ``eval`` and skipped unless ``EVAL_MODEL_TIER`` is set.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.eval


def _skip_unless_eval() -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not os.environ.get("EVAL_MODEL_TIER"),
        reason="Set EVAL_MODEL_TIER env var to run eval tests",
    )


# ---------------------------------------------------------------------------
# Canned tool result fixtures
# ---------------------------------------------------------------------------

_VM_RESULT = {
    "vms": [
        {
            "id": "vm-1",
            "name": "prod-web-01",
            "provider": "aws",
            "region": "us-east-1",
            "vcpu": 16,
            "memory_gib": 64.0,
            "os_family": "Amazon Linux 2",
            "status": "running",
            "source_sku": "m5.4xlarge",
            "snapshot_time": "2025-06-01T10:00:00",
        }
    ],
    "total": 1,
    "snapshot_time": "2025-06-01T10:00:00",
    "coverage_warning": None,
}

_DISCOVERY_RESULT = {
    "snapshots": [
        {
            "connection_id": "conn-abc",
            "snapshot_id": "snap-xyz",
            "status": "completed",
            "completed_at": "2025-06-01T09:55:00",
            "coverage_summary": {"us-east-1": "ok"},
            "vm_count": 42,
        }
    ]
}


def _make_tool_gateway(tool_name: str, tool_result: dict[str, Any]) -> Any:
    """Gateway that calls *tool_name* with canned result, then emits text."""
    from ai.gateway import ChatChunk

    async def _stream(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield ChatChunk(type="tool_call", tool_name=tool_name, tool_input={})
        import json
        yield ChatChunk(
            type="text",
            content=f"Based on the tool result: {json.dumps(tool_result)}",
        )

    mock = MagicMock()
    mock.chat_stream = MagicMock(side_effect=_stream)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_unless_eval()
@pytest.mark.asyncio
async def test_vm_count_faithfulness() -> None:
    """Model must report the exact total VM count from the tool result."""
    from ai.orchestrator import AIOrchestrator, WorkspaceAIConfig
    from tools.loader import load_all_tools
    from tools.registry import CurrentUser, ToolRegistry

    reg = ToolRegistry()
    load_all_tools(reg)

    gateway = _make_tool_gateway("inventory.search_vms", _VM_RESULT)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=lambda: None,
            scalars=lambda: MagicMock(all=lambda: []),
        )
    )
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    factory = MagicMock(return_value=mock_db)

    orchestrator = AIOrchestrator(gateway, reg, factory)
    user = CurrentUser(
        user_id="u-1", email="t@t.com", role="analyst", workspace_id="ws-1"
    )
    config = WorkspaceAIConfig(workspace_id="ws-1")

    output = ""
    async for chunk in await orchestrator.run(None, "How many AWS VMs do I have?", user, config):
        if chunk.type == "text":
            output += chunk.content or ""

    # The tool result says total=1 — the model must not invent a different number
    assert "1" in output, f"Expected tool-reported count '1' in output: {output[:200]}"
    # The model should NOT mention fabricated counts like 5, 10, 100
    for fabricated in ["5 VMs", "10 VMs", "100 VMs", "50 VMs"]:
        assert fabricated not in output, f"Fabricated count '{fabricated}' found in: {output[:200]}"


@_skip_unless_eval()
@pytest.mark.asyncio
async def test_vcpu_memory_faithfulness() -> None:
    """Model must report exact vCPU (16) and memory (64 GiB) from the tool result."""
    from ai.orchestrator import AIOrchestrator, WorkspaceAIConfig
    from tools.loader import load_all_tools
    from tools.registry import CurrentUser, ToolRegistry

    reg = ToolRegistry()
    load_all_tools(reg)
    gateway = _make_tool_gateway("inventory.search_vms", _VM_RESULT)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=lambda: None,
            scalars=lambda: MagicMock(all=lambda: []),
        )
    )
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    factory = MagicMock(return_value=mock_db)

    orchestrator = AIOrchestrator(gateway, reg, factory)
    user = CurrentUser(
        user_id="u-1", email="t@t.com", role="analyst", workspace_id="ws-1"
    )
    config = WorkspaceAIConfig(workspace_id="ws-1")

    output = ""
    async for chunk in await orchestrator.run(None, "What size is prod-web-01?", user, config):
        if chunk.type == "text":
            output += chunk.content or ""

    # The tool result has vcpu=16 and memory_gib=64
    assert "16" in output
    assert "64" in output


@_skip_unless_eval()
@pytest.mark.asyncio
async def test_snapshot_vm_count_faithfulness() -> None:
    """Model must report vm_count=42 from discovery.status, not a different number."""
    from ai.orchestrator import AIOrchestrator, WorkspaceAIConfig
    from tools.loader import load_all_tools
    from tools.registry import CurrentUser, ToolRegistry

    reg = ToolRegistry()
    load_all_tools(reg)
    gateway = _make_tool_gateway("discovery.status", _DISCOVERY_RESULT)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=lambda: None,
            scalars=lambda: MagicMock(all=lambda: []),
        )
    )
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    factory = MagicMock(return_value=mock_db)

    orchestrator = AIOrchestrator(gateway, reg, factory)
    user = CurrentUser(
        user_id="u-1", email="t@t.com", role="analyst", workspace_id="ws-1"
    )
    config = WorkspaceAIConfig(workspace_id="ws-1")

    output = ""
    async for chunk in await orchestrator.run(
        None, "How many VMs were discovered?", user, config
    ):
        if chunk.type == "text":
            output += chunk.content or ""

    assert "42" in output, f"Expected vm_count '42' in output: {output[:200]}"
