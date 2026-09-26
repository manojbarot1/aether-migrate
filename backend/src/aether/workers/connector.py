"""Connector worker: the only process that can read cloud credentials.

Run with ``python -m aether.workers.connector``.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.worker import Worker

from aether.audit.writer import Actor, AuditRecord, record
from aether.config import Settings, get_settings
from aether.core.enums import ActorType, AuditStatus, AuthMethod, ConnectionStatus
from aether.db.models import CloudConnection
from aether.db.session import init_engine, workspace_scope
from aether.logging import configure_logging, get_logger
from aether.providers.aws.adapter import AwsAdapter
from aether.providers.base.adapter import ConnCtx
from aether.secrets.openbao import OpenBaoClient, OpenBaoError
from aether.telemetry import setup_tracing
from aether.temporal_client import connect
from aether.workflows.connection_test import (
    TEST_CONNECTION_ACTIVITY,
    TestConnectionInput,
    TestConnectionWorkflow,
)

log = get_logger(__name__)

PLATFORM_AWS_SECRET_PATH = "platform/aws"
HEARTBEAT_FILE = Path("/tmp/worker-heartbeat")  # noqa: S108 - tmpfs inside the container


class ConnectorActivities:
    def __init__(self, bao: OpenBaoClient, aws: AwsAdapter | None = None) -> None:
        self._bao = bao
        self._aws = aws or AwsAdapter()

    async def _platform_secret(self) -> dict[str, str] | None:
        try:
            return await self._bao.kv_read(PLATFORM_AWS_SECRET_PATH)
        except OpenBaoError as e:
            if e.status == 404:
                return None
            raise

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

    acts = ConnectorActivities(bao)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    worker = Worker(
        client,
        task_queue=settings.connector_task_queue,
        workflows=[TestConnectionWorkflow],
        activities=[acts.test_connection],
        max_concurrent_activities=20,
    )
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
