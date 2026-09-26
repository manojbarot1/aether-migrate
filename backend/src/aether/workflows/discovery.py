"""Discovery workflow (PROJECT_PLAN §11).

list regions → one activity per region (bounded concurrency, heartbeating) → finalise.
Payloads carry identifiers and counts only; resources are written to the database by
the region activities themselves, so large estates never pass through workflow history.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


class DiscoveryInput(BaseModel):
    workspace_id: str
    connection_id: str
    snapshot_id: str
    requested_by: str
    requested_by_display: str | None = None
    request_id: str | None = None


class RegionInput(BaseModel):
    discovery: DiscoveryInput
    region: str


class RegionResult(BaseModel):
    region: str
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: list[dict[str, Any]] = Field(default_factory=list)
    cloud_calls: dict[str, int] = Field(default_factory=dict)


class FinalizeInput(BaseModel):
    discovery: DiscoveryInput
    regions: list[str] = Field(default_factory=list)
    results: list[RegionResult] = Field(default_factory=list)
    fatal_error: str | None = None


LIST_REGIONS_ACTIVITY = "discovery_list_regions"
DISCOVER_REGION_ACTIVITY = "discovery_region"
FINALIZE_ACTIVITY = "discovery_finalize"

REGION_CONCURRENCY = 4

_retry = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_attempts=4,
    non_retryable_error_types=["ValueError", "PermissionError"],
)


@workflow.defn(name="DiscoverConnection")
class DiscoverConnectionWorkflow:
    @workflow.run
    async def run(self, inp: DiscoveryInput) -> dict[str, Any]:
        try:
            regions: list[str] = await workflow.execute_activity(
                LIST_REGIONS_ACTIVITY, inp, start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry
            )
        except ActivityError as e:
            return await self._finalize(FinalizeInput(discovery=inp, fatal_error=_cause(e)))

        sem = asyncio.Semaphore(REGION_CONCURRENCY)

        async def one(region: str) -> RegionResult:
            async with sem:
                try:
                    res: RegionResult = await workflow.execute_activity(
                        DISCOVER_REGION_ACTIVITY,
                        RegionInput(discovery=inp, region=region),
                        result_type=RegionResult,
                        start_to_close_timeout=timedelta(minutes=30),
                        heartbeat_timeout=timedelta(minutes=2),
                        retry_policy=_retry,
                    )
                    return res
                except ActivityError as e:
                    return RegionResult(
                        region=region,
                        coverage=[
                            {"region": region, "kind": "region", "status": "error", "detail": _cause(e)}
                        ],
                    )

        results = list(await asyncio.gather(*(one(r) for r in regions)))
        return await self._finalize(FinalizeInput(discovery=inp, regions=regions, results=results))

    async def _finalize(self, fin: FinalizeInput) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            FINALIZE_ACTIVITY, fin, start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry
        )
        return result


def _cause(e: ActivityError) -> str:
    cause = e.cause
    while getattr(cause, "cause", None) is not None:
        cause = cause.cause  # type: ignore[union-attr]
    return str(cause or e)[:500]
