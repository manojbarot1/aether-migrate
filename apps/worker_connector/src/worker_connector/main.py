"""Connector Temporal worker — registers AWS discovery and catalog sync workflows.

Phase 5 additions:
  - AWSCatalogSyncWorkflow: AWS catalog sync (needs boto3 credentials from OpenBao)


Activities receive a ``connection_id`` and use ``get_aws_credentials()`` to
fetch credentials from OpenBao just-in-time.  Credential values are NEVER
logged — only the ``connection_id`` path is emitted.
"""

from __future__ import annotations

import asyncio
import os

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

TASK_QUEUE = "connector"


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------


async def get_aws_credentials(connection_id: str) -> AWSCredentials:  # noqa: F821
    """Fetch AWS credentials from OpenBao for *connection_id*.

    The credential path is ``{workspace_id}/{connection_id}`` but the worker
    receives only ``connection_id`` in its activity input.  The workspace is
    resolved from the DB row.

    Security: only the path (``connection_id``) is logged, never the values.
    """
    from aws.connection_test import AWSCredentials
    from db.engine import get_session
    from db.models import ConnectionRow
    from secrets_svc import OpenBaoClient
    from sqlalchemy import select

    log.info("worker.fetching_credentials", connection_id=connection_id)

    async with get_session() as session:
        result = await session.execute(
            select(ConnectionRow).where(ConnectionRow.id == connection_id)  # type: ignore[arg-type]
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Connection not found: {connection_id}")
        workspace_id = row.workspace_id

    bao_path = f"{workspace_id}/{connection_id}"
    async with OpenBaoClient() as bao:
        cred_data = await bao.read_credential(bao_path)

    log.info("worker.fetched_credentials", connection_id=connection_id)

    return AWSCredentials(
        role_arn=cred_data.get("aws_role_arn"),
        external_id=cred_data.get("aws_external_id"),
        access_key_id=cred_data.get("aws_access_key_id"),
        secret_access_key=cred_data.get("aws_secret_access_key"),
        default_region=cred_data.get("aws_default_region", "us-east-1"),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _ensure_aws_catalog_schedule(client: Client) -> None:
    """Create the AWS catalog sync daily schedule if it does not exist."""
    from catalog.scheduler import AWSCatalogSyncWorkflow

    schedule_id = "aws-catalog-sync-daily"
    connection_id = os.environ.get("AWS_CATALOG_CONNECTION_ID", "")
    if not connection_id:
        log.warning("worker_connector.aws_catalog_connection_id_not_set")
        return

    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.describe()
        log.info("worker_connector.schedule_exists", schedule_id=schedule_id)
    except Exception:  # noqa: BLE001
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleCronString,
            ScheduleSpec,
        )

        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    AWSCatalogSyncWorkflow.run,
                    connection_id,
                    id="aws-catalog-sync",
                    task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(
                    cron_expressions=[ScheduleCronString("0 3 * * *")],
                ),
            ),
        )
        log.info("worker_connector.schedule_created", schedule_id=schedule_id)


async def main() -> None:
    from aws.discovery import (
        AWSDiscoveryWorkflow,
        _create_snapshot_activity,
        _list_regions_activity,
        _update_snapshot_activity,
        discover_region_activity,
    )
    from catalog.scheduler import (
        AWSCatalogSyncWorkflow,
        emit_catalog_staleness_activity,
        sync_aws_catalog_activity,
    )

    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")

    log.info("worker_connector_connecting", host=temporal_host, namespace=namespace)
    client = await Client.connect(temporal_host, namespace=namespace)

    await _ensure_aws_catalog_schedule(client)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AWSDiscoveryWorkflow, AWSCatalogSyncWorkflow],
        activities=[
            discover_region_activity,
            _create_snapshot_activity,
            _update_snapshot_activity,
            _list_regions_activity,
            sync_aws_catalog_activity,
            emit_catalog_staleness_activity,
        ],
    )

    log.info("worker_connector_started", task_queue=TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
