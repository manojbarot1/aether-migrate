"""Catalog sync: target-cloud prices and specs, plus FX rates. Scheduled daily."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy


class CatalogSyncInput(BaseModel):
    regions: list[str] = Field(default_factory=list)
    requested_by: str | None = None


SYNC_AZURE_ACTIVITY = "catalog_sync_azure"
SYNC_FX_ACTIVITY = "catalog_sync_fx"
SCHEDULE_ID = "catalog-daily"


@workflow.defn(name="SyncCatalog")
class SyncCatalogWorkflow:
    @workflow.run
    async def run(self, inp: CatalogSyncInput) -> dict[str, Any]:
        retry = RetryPolicy(initial_interval=timedelta(seconds=10), maximum_attempts=4)
        fx: dict[str, Any] = await workflow.execute_activity(
            SYNC_FX_ACTIVITY, start_to_close_timeout=timedelta(minutes=2), retry_policy=retry
        )
        azure: dict[str, Any] = await workflow.execute_activity(
            SYNC_AZURE_ACTIVITY,
            inp,
            start_to_close_timeout=timedelta(minutes=20),
            heartbeat_timeout=timedelta(minutes=3),
            retry_policy=retry,
        )
        return {"fx": fx, "azure": azure}
