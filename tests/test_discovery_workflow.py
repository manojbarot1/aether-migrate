"""Temporal workflow unit tests for AWSDiscoveryWorkflow.

Uses temporalio.testing.WorkflowEnvironment (time-skipping mode) to run the
workflow without a real Temporal server.

Activities are mocked with patch-based overrides.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from aws.discovery import (
    AWSDiscoveryInput,
    AWSDiscoveryWorkflow,
    DiscoverRegionInput,
    DiscoverRegionResult,
)
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

SNAPSHOT_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_CONNECTION_ID = str(uuid.uuid4())
_FAKE_WORKSPACE_ID = str(uuid.uuid4())


# Mock activities — replace DB / boto3 calls with in-memory stubs.

@activity.defn(name="CreateSnapshotActivity")
async def mock_create_snapshot(connection_id: str, workspace_id: str) -> str:
    return SNAPSHOT_ID


@activity.defn(name="ListRegionsActivity")
async def mock_list_regions(connection_id: str) -> list[str]:
    return ["us-east-1", "eu-west-1"]


@activity.defn(name="UpdateSnapshotActivity")
async def mock_update_snapshot(snapshot_id: str, status: str, coverage: dict[str, str]) -> None:
    # Track calls in a module-level dict for test assertions
    _update_calls[snapshot_id] = (status, coverage)


_update_calls: dict[str, tuple[str, dict]] = {}


@activity.defn(name="DiscoverRegionActivity")
async def mock_discover_region(inp: DiscoverRegionInput) -> DiscoverRegionResult:
    return DiscoverRegionResult(
        region=inp.region,
        resource_kind=inp.resource_kind,
        status="ok",
        count=5,
    )


@activity.defn(name="DiscoverRegionActivity")
async def mock_discover_region_fail(inp: DiscoverRegionInput) -> DiscoverRegionResult:
    return DiscoverRegionResult(
        region=inp.region,
        resource_kind=inp.resource_kind,
        status="error",
        error="Simulated error",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_creates_snapshot_and_completes() -> None:
    """Workflow creates snapshot, fans out activities, marks snapshot completed."""
    _update_calls.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-connector",
            workflows=[AWSDiscoveryWorkflow],
            activities=[
                mock_create_snapshot,
                mock_list_regions,
                mock_update_snapshot,
                mock_discover_region,
            ],
        ):
            result = await env.client.execute_workflow(
                AWSDiscoveryWorkflow.run,
                AWSDiscoveryInput(
                    connection_id=_FAKE_CONNECTION_ID,
                    workspace_id=_FAKE_WORKSPACE_ID,
                    regions=["us-east-1"],
                    resource_kinds=["vm", "disk"],
                ),
                id=f"test-{uuid.uuid4()}",
                task_queue="test-connector",
                execution_timeout=timedelta(minutes=1),
            )

    assert result.snapshot_id == SNAPSHOT_ID
    assert result.status == "completed"

    # Coverage should have entries for us-east-1 × vm and us-east-1 × disk
    assert "us-east-1/vm" in result.coverage
    assert "us-east-1/disk" in result.coverage
    assert all(v == "ok" for v in result.coverage.values())


@pytest.mark.asyncio
async def test_workflow_coverage_report_correct_keys() -> None:
    """Coverage report contains region/kind keys for all fan-out pairs."""
    _update_calls.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-connector",
            workflows=[AWSDiscoveryWorkflow],
            activities=[
                mock_create_snapshot,
                mock_list_regions,
                mock_update_snapshot,
                mock_discover_region,
            ],
        ):
            result = await env.client.execute_workflow(
                AWSDiscoveryWorkflow.run,
                AWSDiscoveryInput(
                    connection_id=_FAKE_CONNECTION_ID,
                    workspace_id=_FAKE_WORKSPACE_ID,
                    regions=["us-east-1", "eu-west-1"],
                    resource_kinds=["vm"],
                ),
                id=f"test-{uuid.uuid4()}",
                task_queue="test-connector",
                execution_timeout=timedelta(minutes=1),
            )

    expected_keys = {"us-east-1/vm", "eu-west-1/vm"}
    assert expected_keys == set(result.coverage.keys())


@pytest.mark.asyncio
async def test_snapshot_update_called_with_completed() -> None:
    """UpdateSnapshotActivity is called with status=completed at the end."""
    _update_calls.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-connector",
            workflows=[AWSDiscoveryWorkflow],
            activities=[
                mock_create_snapshot,
                mock_list_regions,
                mock_update_snapshot,
                mock_discover_region,
            ],
        ):
            await env.client.execute_workflow(
                AWSDiscoveryWorkflow.run,
                AWSDiscoveryInput(
                    connection_id=_FAKE_CONNECTION_ID,
                    workspace_id=_FAKE_WORKSPACE_ID,
                    regions=["us-east-1"],
                    resource_kinds=["vm"],
                ),
                id=f"test-{uuid.uuid4()}",
                task_queue="test-connector",
                execution_timeout=timedelta(minutes=1),
            )

    assert SNAPSHOT_ID in _update_calls
    status, _ = _update_calls[SNAPSHOT_ID]
    assert status == "completed"
