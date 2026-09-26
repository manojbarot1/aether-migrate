"""Connector worker: the only process that can read cloud credentials.

Run with ``python -m aether.workers.connector``.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from temporalio import activity
from temporalio.worker import Worker

from aether.audit.writer import Actor, AuditRecord, record
from aether.catalog import aws as aws_prices
from aether.catalog import azure as azure_catalog
from aether.catalog.fx import fetch_ecb
from aether.catalog.store import upsert_disks, upsert_fx, upsert_prices, upsert_specs
from aether.config import Settings, get_settings
from aether.core.enums import ActorType, AuditStatus, AuthMethod, ConnectionStatus
from aether.core.inventory import CoverageEntry, CoverageStatus
from aether.db.models import CatalogSync, CloudConnection, Snapshot
from aether.db.session import init_engine, session_scope, workspace_scope
from aether.inventory.store import write_bundle
from aether.logging import configure_logging, get_logger
from aether.providers.aws.adapter import BOTO_CONFIG, AwsAdapter, CallRecorder
from aether.providers.aws.discovery import collect_region, list_enabled_regions
from aether.providers.aws.normalize import normalize_region
from aether.providers.base.adapter import ConnCtx
from aether.secrets.openbao import OpenBaoClient, OpenBaoError
from aether.telemetry import setup_tracing
from aether.temporal_client import connect
from aether.workflows.catalog import (
    SCHEDULE_ID,
    SYNC_AZURE_ACTIVITY,
    SYNC_FX_ACTIVITY,
    CatalogSyncInput,
    SyncCatalogWorkflow,
)
from aether.workflows.connection_test import (
    TEST_CONNECTION_ACTIVITY,
    TestConnectionInput,
    TestConnectionWorkflow,
)
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

log = get_logger(__name__)

PLATFORM_AWS_SECRET_PATH = "platform/aws"
HEARTBEAT_FILE = Path("/tmp/worker-heartbeat")  # noqa: S108 - tmpfs inside the container


def _classify_pricing_error(e: Exception) -> tuple[CoverageStatus, str]:
    from botocore.exceptions import ClientError

    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "ClientError")
        denied = code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation")
        return (CoverageStatus.DENIED if denied else CoverageStatus.ERROR), code
    return CoverageStatus.ERROR, type(e).__name__


def _safe_heartbeat(detail: str) -> None:
    with contextlib.suppress(RuntimeError):  # not running inside an activity (tests)
        activity.heartbeat(detail)


class ConnectorActivities:
    def __init__(
        self, bao: OpenBaoClient, aws: AwsAdapter | None = None, settings: Settings | None = None
    ) -> None:
        self._bao = bao
        self._aws = aws or AwsAdapter()
        self._settings = settings or get_settings()

    async def _platform_secret(self) -> dict[str, str] | None:
        try:
            return await self._bao.kv_read(PLATFORM_AWS_SECRET_PATH)
        except OpenBaoError as e:
            if e.status == 404:
                return None
            raise

    async def _ctx(self, ws: uuid.UUID, conn_id: uuid.UUID) -> ConnCtx:
        """Load connection metadata (RLS-scoped) and its secrets from OpenBao."""
        async with workspace_scope(ws) as s:
            conn = await s.get(CloudConnection, conn_id)
            if conn is None or conn.workspace_id != ws:
                raise ValueError("connection not found")
            ctx = ConnCtx(connection_id=str(conn_id), auth_method=conn.auth_method, config=dict(conn.config))
            secret_path, secret_version = conn.secret_path, conn.secret_version
        if secret_path:
            ctx.secret = await self._bao.kv_read(secret_path, secret_version)
        if ctx.auth_method == AuthMethod.AWS_ASSUME_ROLE:
            ctx.platform_secret = await self._platform_secret()
        return ctx

    # ------------------------------------------------------------------ discovery

    @activity.defn(name=LIST_REGIONS_ACTIVITY)
    async def discovery_list_regions(self, inp: DiscoveryInput) -> list[str]:
        ctx = await self._ctx(uuid.UUID(inp.workspace_id), uuid.UUID(inp.connection_id))
        try:
            session = await asyncio.to_thread(self._aws.session, ctx, CallRecorder())
            enabled = await asyncio.to_thread(list_enabled_regions, session)
        finally:
            ctx.secret = ctx.platform_secret = None
        configured = list(ctx.config.get("regions") or [])
        regions = [r for r in configured if r in enabled] if configured else enabled
        async with workspace_scope(uuid.UUID(inp.workspace_id)) as s:
            snap = await s.get(Snapshot, uuid.UUID(inp.snapshot_id))
            if snap is not None:
                snap.regions = regions
        return regions

    @activity.defn(name=DISCOVER_REGION_ACTIVITY)
    async def discovery_region(self, inp: RegionInput) -> RegionResult:
        d = inp.discovery
        ws, conn_id, snap_id = uuid.UUID(d.workspace_id), uuid.UUID(d.connection_id), uuid.UUID(d.snapshot_id)
        ctx = await self._ctx(ws, conn_id)
        recorder = CallRecorder()
        loop = asyncio.get_running_loop()

        def heartbeat(detail: str) -> None:
            # Called from the worker thread; activity.heartbeat must run on the loop.
            loop.call_soon_threadsafe(_safe_heartbeat, detail)

        def collect() -> tuple[Any, Any]:
            session = self._aws.session(ctx, recorder, region=inp.region)
            account = session.client("sts", config=BOTO_CONFIG).get_caller_identity()["Account"]
            return collect_region(session, account, inp.region, heartbeat)

        def collect_prices(session: Any, skus: set[str]) -> tuple[Any, Any]:
            return aws_prices.fetch_prices(session, inp.region, skus)

        try:
            raw, coverage = await asyncio.to_thread(collect)
            # Source list prices for the SKUs we found (global catalog data). Best effort:
            # a missing pricing permission degrades cost comparison, not discovery.
            skus = {i.get("InstanceType") for i in raw.instances if i.get("InstanceType")}
            if skus:
                try:
                    session = await asyncio.to_thread(self._aws.session, ctx, recorder, inp.region)
                    src_prices, src_disks = await asyncio.to_thread(collect_prices, session, skus)
                    async with session_scope() as cs:
                        await upsert_prices(cs, src_prices, None)
                        await upsert_disks(cs, src_disks)
                    coverage.append(
                        CoverageEntry(region=inp.region, kind="pricing:ec2", status=CoverageStatus.OK)
                    )
                except Exception as e:
                    status, detail = _classify_pricing_error(e)
                    coverage.append(
                        CoverageEntry(region=inp.region, kind="pricing:ec2", status=status, detail=detail)
                    )
        finally:
            ctx.secret = ctx.platform_secret = None
        bundle = normalize_region(snap_id, raw)
        bundle.coverage = coverage
        async with workspace_scope(ws) as s:
            counts = await write_bundle(
                s, workspace_id=ws, snapshot_id=snap_id, connection_id=conn_id, provider="aws", bundle=bundle
            )
        calls: dict[str, int] = {}
        for c in recorder.calls:
            op = c.rsplit(":", 1)[0]
            calls[op] = calls.get(op, 0) + 1
        return RegionResult(
            region=inp.region,
            counts=counts,
            coverage=[c.model_dump(mode="json") for c in coverage],
            cloud_calls=calls,
        )

    @activity.defn(name=FINALIZE_ACTIVITY)
    async def discovery_finalize(self, fin: FinalizeInput) -> dict[str, Any]:
        d = fin.discovery
        ws, snap_id = uuid.UUID(d.workspace_id), uuid.UUID(d.snapshot_id)
        coverage = [c for r in fin.results for c in r.coverage]
        totals: dict[str, int] = {}
        calls: dict[str, int] = {}
        for r in fin.results:
            for k, v in r.counts.items():
                totals[k] = totals.get(k, 0) + v
            for k, v in r.cloud_calls.items():
                calls[k] = calls.get(k, 0) + v
        if fin.fatal_error:
            status = "failed"
        elif any(
            c.get("status") != CoverageStatus.OK
            for c in coverage
            if not str(c.get("kind", "")).startswith("pricing:")
        ):
            status = "partial"
        else:
            status = "complete"
        stats = {"resources": totals, "regions": len(fin.regions), "cloud_calls": sum(calls.values())}
        async with workspace_scope(ws) as s:
            snap = await s.get(Snapshot, snap_id)
            if snap is None:
                raise ValueError("snapshot not found")
            snap.status = status
            snap.finished_at = datetime.now(UTC)
            snap.coverage = coverage
            snap.stats = stats
            snap.error = fin.fatal_error
            await record(
                s,
                AuditRecord(
                    actor=Actor(ActorType.USER, d.requested_by, d.requested_by_display),
                    action="discovery.run",
                    status=AuditStatus.FAILURE if status == "failed" else AuditStatus.SUCCESS,
                    workspace_id=ws,
                    connection_id=uuid.UUID(d.connection_id),
                    target_type="snapshot",
                    target_id=str(snap_id),
                    details={
                        "result": status,
                        "regions": fin.regions,
                        "resources": totals,
                        "coverage_gaps": [c for c in coverage if c.get("status") != "ok"],
                        "cloud_calls": calls,
                        "error": fin.fatal_error,
                        "executed_by": "worker-connector",
                    },
                    request_id=d.request_id,
                ),
            )
        log.info("discovery.finished", snapshot_id=str(snap_id), status=status, **{"resources": totals})
        return {"status": status, **stats}

    # ------------------------------------------------------------------ catalog

    @activity.defn(name=SYNC_FX_ACTIVITY)
    async def catalog_sync_fx(self) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            rates = await fetch_ecb(client)
        async with session_scope() as s:
            n = await upsert_fx(s, rates)
        return {"currencies": n, "rate_date": str(rates[0].rate_date)}

    @activity.defn(name=SYNC_AZURE_ACTIVITY)
    async def catalog_sync_azure(self, inp: CatalogSyncInput) -> dict[str, Any]:
        regions = inp.regions or self._settings.catalog_azure_regions
        sync = CatalogSync(id=uuid.uuid4(), provider="azure", status="running", regions=regions)
        async with session_scope() as s:
            s.add(sync)
            await upsert_specs(s, azure_catalog.curated_specs())
        stats: dict[str, Any] = {}
        errors: dict[str, str] = {}
        async with httpx.AsyncClient() as client:
            for region in regions:
                activity.heartbeat(region)
                try:
                    prices, disks = await azure_catalog.fetch_region(client, region)
                except httpx.HTTPError as e:
                    errors[region] = type(e).__name__
                    continue
                async with session_scope() as s:
                    stats[region] = {
                        "prices": await upsert_prices(s, prices, sync.id),
                        "disk_tiers": await upsert_disks(s, disks),
                    }
        async with session_scope() as s:
            row = await s.get(CatalogSync, sync.id)
            if row is not None:
                row.status = "complete" if not errors else ("partial" if stats else "failed")
                row.finished_at = datetime.now(UTC)
                row.stats = {"regions": stats, "errors": errors}
                await record(
                    s,
                    AuditRecord(
                        actor=Actor(ActorType.SYSTEM, "catalog-sync", "catalog-sync")
                        if not inp.requested_by
                        else Actor(ActorType.USER, inp.requested_by, None),
                        action="catalog.sync",
                        status=AuditStatus.SUCCESS if not errors else AuditStatus.FAILURE,
                        target_type="catalog",
                        target_id="azure",
                        details={"regions": regions, "stats": stats, "errors": errors},
                    ),
                )
        return {"regions": stats, "errors": errors}

    # ------------------------------------------------------------------ connection test

    @activity.defn(name=TEST_CONNECTION_ACTIVITY)
    async def test_connection(self, inp: TestConnectionInput) -> dict[str, Any]:
        ws = uuid.UUID(inp.workspace_id)
        conn_id = uuid.UUID(inp.connection_id)
        async with workspace_scope(ws) as s:
            conn = await s.get(CloudConnection, conn_id)
            if conn is None or conn.workspace_id != ws:
                raise ValueError("connection not found")
            auth_method, config = conn.auth_method, dict(conn.config)
            secret_path, secret_version = conn.secret_path, conn.secret_version

        ctx = ConnCtx(connection_id=str(conn_id), auth_method=auth_method, config=config)
        if secret_path:
            ctx.secret = await self._bao.kv_read(secret_path, secret_version)
        if auth_method == AuthMethod.AWS_ASSUME_ROLE:
            ctx.platform_secret = await self._platform_secret()

        try:
            result = await asyncio.to_thread(self._aws.test_connection, ctx)
        finally:
            # Drop secret material as soon as the cloud calls are done.
            ctx.secret = None
            ctx.platform_secret = None

        payload = result.model_dump(mode="json")
        async with workspace_scope(ws) as s:
            conn = await s.get(CloudConnection, conn_id)
            if conn is not None:
                conn.status = result.status.value
                conn.last_tested_at = datetime.now(UTC)
                conn.last_test_result = payload
            await record(
                s,
                AuditRecord(
                    actor=Actor(ActorType.USER, inp.requested_by, inp.requested_by_display),
                    action="connection.test",
                    status=AuditStatus.SUCCESS
                    if result.status != ConnectionStatus.ERROR
                    else AuditStatus.FAILURE,
                    workspace_id=ws,
                    connection_id=conn_id,
                    target_type="connection",
                    target_id=str(conn_id),
                    details={
                        "result": result.status.value,
                        "identity": result.identity,
                        "checks": [{"id": c.id, "status": c.status.value} for c in result.checks],
                        "cloud_calls": result.cloud_calls,
                        "executed_by": "worker-connector",
                    },
                    request_id=inp.request_id,
                ),
            )
        log.info("connection.tested", connection_id=str(conn_id), status=result.status.value)
        return payload


async def _heartbeat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(HEARTBEAT_FILE.write_text, datetime.now(UTC).isoformat())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=15)


async def _ensure_catalog_schedule(client: Any, settings: Settings) -> None:
    """Daily catalog refresh; created once, idempotently. Runs immediately on first creation."""
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleAlreadyRunningError,
        ScheduleIntervalSpec,
        ScheduleSpec,
    )

    try:
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    SyncCatalogWorkflow.run,
                    CatalogSyncInput(),
                    id="catalog-sync-scheduled",
                    task_queue=settings.connector_task_queue,
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(hours=24))]),
            ),
            trigger_immediately=True,
        )
        log.info("catalog.schedule_created")
    except ScheduleAlreadyRunningError:
        pass


async def run(settings: Settings) -> None:
    configure_logging(settings.log_level, settings.log_json)
    setup_tracing(settings)
    init_engine(settings)
    bao = OpenBaoClient.from_settings(settings)
    client = None
    for attempt in range(30):
        try:
            client = await connect(settings)
            break
        except Exception as e:
            log.warning("temporal.connect_retry", attempt=attempt, error=type(e).__name__)
            await asyncio.sleep(2)
    if client is None:
        raise RuntimeError("could not connect to Temporal")

    acts = ConnectorActivities(bao, settings=settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    worker = Worker(
        client,
        task_queue=settings.connector_task_queue,
        workflows=[TestConnectionWorkflow, DiscoverConnectionWorkflow, SyncCatalogWorkflow],
        activities=[
            acts.catalog_sync_fx,
            acts.catalog_sync_azure,
            acts.test_connection,
            acts.discovery_list_regions,
            acts.discovery_region,
            acts.discovery_finalize,
        ],
        max_concurrent_activities=20,
    )
    await _ensure_catalog_schedule(client, settings)
    log.info("worker.started", task_queue=settings.connector_task_queue)
    hb = asyncio.create_task(_heartbeat(stop))
    async with worker:
        await stop.wait()
    await hb
    await bao.aclose()
    log.info("worker.stopped")


def main() -> None:
    settings = get_settings().model_copy(update={"service_name": "aether-worker-connector"})
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
