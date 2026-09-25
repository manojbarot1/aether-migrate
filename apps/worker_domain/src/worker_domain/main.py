"""Domain Temporal worker — registers domain and catalog sync workflows.

Phase 5 additions:
  - AzureCatalogSyncWorkflow: Azure catalog sync (public API, no cloud auth)
  - FX rate sync runs as part of AzureCatalogSyncWorkflow
"""

from __future__ import annotations

import asyncio
import os

import structlog
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleCronString,
    ScheduleSpec,
)
from temporalio.worker import Worker
from temporalio.workflow import defn, run

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

TASK_QUEUE = "domain"


@defn
class PlaceholderWorkflow:
    """Placeholder workflow — replaced by domain workflows in Phase 3/4."""

    @run
    async def run(self) -> str:
        return "placeholder"


async def _ensure_azure_catalog_schedule(client: Client) -> None:
    """Create the Azure catalog sync daily schedule if it does not exist."""
    from catalog.scheduler import AzureCatalogSyncWorkflow

    schedule_id = "azure-catalog-sync-daily"
    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.describe()
        log.info("worker_domain.schedule_exists", schedule_id=schedule_id)
    except Exception:  # noqa: BLE001
        # Schedule does not exist — create it
        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    AzureCatalogSyncWorkflow.run,
                    id="azure-catalog-sync",
                    task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(
                    cron_expressions=[ScheduleCronString("0 2 * * *")],
                ),
            ),
        )
        log.info("worker_domain.schedule_created", schedule_id=schedule_id)


async def main() -> None:
    from catalog.scheduler import (
        AzureCatalogSyncWorkflow,
        emit_catalog_staleness_activity,
        sync_azure_catalog_activity,
        sync_fx_rates_activity,
    )

    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")

    log.info("worker_domain_connecting", host=temporal_host, namespace=namespace)
    client = await Client.connect(temporal_host, namespace=namespace)

    await _ensure_azure_catalog_schedule(client)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PlaceholderWorkflow, AzureCatalogSyncWorkflow],
        activities=[
            sync_azure_catalog_activity,
            sync_fx_rates_activity,
            emit_catalog_staleness_activity,
        ],
    )

    log.info("worker_domain_started", task_queue=TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
