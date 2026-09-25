"""AETHER MIGRATE — catalog sync Temporal workflows.

Workflows are split by auth requirement:
  - AzureCatalogSyncWorkflow: uses public Azure Retail Prices API (no auth);
    registered in worker_domain.
  - AWSCatalogSyncWorkflow: needs boto3/credentials from OpenBao;
    registered in worker_connector.

Staleness alert metric:
  - catalog.last_synced_at (gauge) emitted per provider on success
  - catalog.is_stale (gauge, 1=stale) emitted when catalog > 48h old
"""

from __future__ import annotations

import os
from datetime import UTC, timedelta
from typing import Any

import structlog
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

log = structlog.get_logger(__name__)

# Default Azure regions to sync
_DEFAULT_AZURE_REGIONS = [
    "eastus",
    "westeurope",
    "northeurope",
    "uksouth",
    "westus2",
    "southeastasia",
]

# Default AWS regions to sync
_DEFAULT_AWS_REGIONS = [
    "us-east-1",
    "us-west-2",
    "eu-west-1",
    "eu-central-1",
    "ap-southeast-1",
    "ap-northeast-1",
]

_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_attempts=3,
    maximum_interval=timedelta(minutes=10),
)


# ---------------------------------------------------------------------------
# Activity implementations
# ---------------------------------------------------------------------------


@activity.defn(name="sync_azure_catalog_activity")
async def sync_azure_catalog_activity(regions: list[str]) -> dict[str, Any]:
    """Sync Azure instance types and prices for the given regions."""
    from db.engine import get_session

    from catalog.sync.azure import AzureCatalogSync

    syncer = AzureCatalogSync()
    results: dict[str, Any] = {"instance_types": {}, "prices": {}}

    async with get_session() as db:
        it_result = await syncer.sync_instance_types(regions, db)
        results["instance_types"] = it_result.model_dump()

        price_result = await syncer.sync_prices(regions, db)
        results["prices"] = price_result.model_dump()

    return results


@activity.defn(name="sync_aws_catalog_activity")
async def sync_aws_catalog_activity(
    regions: list[str],
    connection_id: str,
) -> dict[str, Any]:
    """Sync AWS instance types and prices for the given regions.

    Requires a connection_id to fetch AWS credentials from OpenBao.
    """
    from db.engine import get_session

    from catalog.sync.aws import AWSCatalogSync

    syncer = AWSCatalogSync()
    results: dict[str, Any] = {"instance_types": {}, "prices": {}}

    # Fetch credentials via the worker helper (only path is logged, not values)
    from worker_connector.main import get_aws_credentials  # type: ignore[import]

    creds = await get_aws_credentials(connection_id)

    import boto3

    boto3_session = boto3.Session(
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
        region_name=creds.default_region or "us-east-1",
    )

    async with get_session() as db:
        it_result = await syncer.sync_instance_types(regions, db, boto3_session=boto3_session)
        results["instance_types"] = it_result.model_dump()

        price_result = await syncer.sync_prices(regions, db, boto3_session=boto3_session)
        results["prices"] = price_result.model_dump()

    return results


@activity.defn(name="sync_fx_rates_activity")
async def sync_fx_rates_activity() -> dict[str, Any]:
    """Sync FX rates from ECB."""
    from db.engine import get_session

    from catalog.sync.fx import FXRateSync

    syncer = FXRateSync()
    async with get_session() as db:
        result = await syncer.sync(db)
    return result


@activity.defn(name="emit_catalog_staleness_activity")
async def emit_catalog_staleness_activity(provider: str) -> dict[str, Any]:
    """Check catalog freshness and emit Prometheus-style metrics via structlog.

    Emits:
      - catalog.last_synced_at (epoch seconds) — for Grafana
      - catalog.is_stale (0|1) — alerts when 1
    """

    from db.engine import get_session

    from catalog.versioning import get_latest_catalog_version, is_catalog_stale

    async with get_session() as db:
        stale = await is_catalog_stale(db, provider)
        version_info = await get_latest_catalog_version(db, provider)

    last_synced_epoch = 0.0
    if version_info:
        _, eff = version_info
        if eff.tzinfo is None:
            eff = eff.replace(tzinfo=UTC)
        last_synced_epoch = eff.timestamp()

    log.info(
        "catalog.metrics",
        provider=provider,
        metric_catalog_last_synced_at=last_synced_epoch,
        metric_catalog_is_stale=int(stale),
    )

    return {
        "provider": provider,
        "is_stale": stale,
        "last_synced_at": last_synced_epoch,
    }


# ---------------------------------------------------------------------------
# Azure Catalog Sync Workflow (no auth — worker_domain)
# ---------------------------------------------------------------------------


@workflow.defn(name="AzureCatalogSyncWorkflow")
class AzureCatalogSyncWorkflow:
    """Daily Azure catalog sync workflow.

    Cron schedule: "0 2 * * *" — runs at 02:00 UTC every day.
    Register in worker_domain (no cloud auth required).
    """

    @workflow.run
    async def run(self) -> dict[str, Any]:
        regions_env = os.environ.get("AZURE_CATALOG_REGIONS", "")
        regions = [r.strip() for r in regions_env.split(",") if r.strip()] or _DEFAULT_AZURE_REGIONS

        # Sync instance types and prices
        results = await workflow.execute_activity(
            sync_azure_catalog_activity,
            regions,
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=_RETRY_POLICY,
        )

        # Sync FX rates
        fx_result = await workflow.execute_activity(
            sync_fx_rates_activity,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY_POLICY,
        )
        results["fx"] = fx_result

        # Emit staleness metric
        await workflow.execute_activity(
            emit_catalog_staleness_activity,
            "azure",
            start_to_close_timeout=timedelta(minutes=2),
        )

        return results


# ---------------------------------------------------------------------------
# AWS Catalog Sync Workflow (needs credentials — worker_connector)
# ---------------------------------------------------------------------------


@workflow.defn(name="AWSCatalogSyncWorkflow")
class AWSCatalogSyncWorkflow:
    """Daily AWS catalog sync workflow.

    Cron schedule: "0 3 * * *" — runs at 03:00 UTC every day.
    Register in worker_connector (needs boto3 credentials).
    """

    @workflow.run
    async def run(self, connection_id: str) -> dict[str, Any]:
        regions_env = os.environ.get("AWS_CATALOG_REGIONS", "")
        regions = [r.strip() for r in regions_env.split(",") if r.strip()] or _DEFAULT_AWS_REGIONS

        results = await workflow.execute_activity(
            sync_aws_catalog_activity,
            args=[regions, connection_id],
            start_to_close_timeout=timedelta(hours=4),
            retry_policy=_RETRY_POLICY,
        )

        # Emit staleness metric
        await workflow.execute_activity(
            emit_catalog_staleness_activity,
            "aws",
            start_to_close_timeout=timedelta(minutes=2),
        )

        return results
