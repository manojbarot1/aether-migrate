"""Workflow orchestration on Temporal's test server, with stub activities."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from aether.workflows.discovery import (
    DISCOVER_REGION_ACTIVITY,
    FINALIZE_ACTIVITY,
    LIST_REGIONS_ACTIVITY,
    DiscoverConnectionWorkflow,
    DiscoveryInput,
    FinalizeInput,
    RegionInput,
    RegionResult,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def env():  # type: ignore[no-untyped-def]
    try:
        e = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # the test server binary is downloaded on first use
        pytest.skip(f"temporal test server unavailable: {exc}")
    yield e
    await e.shutdown()


def _inp() -> DiscoveryInput:
    return DiscoveryInput(
        workspace_id=str(uuid.uuid4()),
        connection_id=str(uuid.uuid4()),
        snapshot_id=str(uuid.uuid4()),
        requested_by="u",
    )


async def _run(env: WorkflowEnvironment, activities: list[Any]) -> dict[str, Any]:
    queue = f"q-{uuid.uuid4()}"
    async with Worker(
        env.client, task_queue=queue, workflows=[DiscoverConnectionWorkflow], activities=activities
    ):
        result: dict[str, Any] = await env.client.execute_workflow(
            DiscoverConnectionWorkflow.run, _inp(), id=f"wf-{uuid.uuid4()}", task_queue=queue
        )
        return result


async def test_regions_fan_out_and_failures_become_coverage(env: WorkflowEnvironment) -> None:
    seen: list[FinalizeInput] = []

    @activity.defn(name=LIST_REGIONS_ACTIVITY)
    async def list_regions(_: DiscoveryInput) -> list[str]:
        return ["eu-west-1", "us-east-1", "ap-south-1"]

    @activity.defn(name=DISCOVER_REGION_ACTIVITY)
    async def region(inp: RegionInput) -> RegionResult:
        if inp.region == "ap-south-1":
            raise ApplicationError("boom", non_retryable=True)
        return RegionResult(
            region=inp.region,
            counts={"vm": 2},
            coverage=[{"region": inp.region, "kind": "ec2:instances", "status": "ok"}],
        )

    @activity.defn(name=FINALIZE_ACTIVITY)
    async def finalize(fin: FinalizeInput) -> dict[str, Any]:
        seen.append(fin)
        return {"status": "partial"}

    assert await _run(env, [list_regions, region, finalize]) == {"status": "partial"}
    fin = seen[0]
    assert fin.fatal_error is None
    assert sorted(r.region for r in fin.results) == ["ap-south-1", "eu-west-1", "us-east-1"]
    failed = next(r for r in fin.results if r.region == "ap-south-1")
    assert failed.coverage[0]["status"] == "error"
    assert "boom" in failed.coverage[0]["detail"]


async def test_auth_failure_finalizes_as_failed(env: WorkflowEnvironment) -> None:
    seen: list[FinalizeInput] = []

    @activity.defn(name=LIST_REGIONS_ACTIVITY)
    async def list_regions(_: DiscoveryInput) -> list[str]:
        raise ApplicationError("InvalidClientTokenId", non_retryable=True)

    @activity.defn(name=DISCOVER_REGION_ACTIVITY)
    async def region(inp: RegionInput) -> RegionResult:  # pragma: no cover - must not run
        raise AssertionError("regions must not be scanned after an auth failure")

    @activity.defn(name=FINALIZE_ACTIVITY)
    async def finalize(fin: FinalizeInput) -> dict[str, Any]:
        seen.append(fin)
        return {"status": "failed"}

    assert await _run(env, [list_regions, region, finalize]) == {"status": "failed"}
    assert seen[0].fatal_error is not None
    assert "InvalidClientTokenId" in seen[0].fatal_error
