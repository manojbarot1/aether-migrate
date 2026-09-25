"""AWS Discovery Temporal Workflow and Activity.

Workflow: AWSDiscoveryWorkflow
  1. Start a SnapshotRow in DB with status=running.
  2. List enabled regions (or use caller-supplied list).
  3. Fan out: for each region × resource_kind, run DiscoverRegionActivity.
     Max 10 concurrent activities (DISCOVERY_CONCURRENCY).
  4. Aggregate coverage, mark snapshot completed.
  5. On failure: mark snapshot failed.

Activity: DiscoverRegionActivity
  1. Fetch credentials from OpenBao (via get_aws_credentials).
  2. Call boto3 paginators for the given resource_kind.
  3. Normalize raw responses.
  4. Upsert ResourceRow records (idempotent on native_id + snapshot_id).
  5. Return a coverage status: ok | denied | throttled-partial | error.

Environment variables:
    DISCOVERY_CONCURRENCY: max concurrent boto3 calls per connection (default 10).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

log = structlog.get_logger(__name__)

_CONCURRENCY = int(os.environ.get("DISCOVERY_CONCURRENCY", "10"))

# Default resource kinds to discover when the caller does not specify.
_DEFAULT_KINDS = ["vm", "disk", "network", "subnet", "security_group", "load_balancer"]

# ---------------------------------------------------------------------------
# Input / result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AWSDiscoveryInput:
    """Input for AWSDiscoveryWorkflow."""

    connection_id: str
    workspace_id: str
    regions: list[str] | None = None
    resource_kinds: list[str] | None = None


@dataclass
class DiscoverRegionInput:
    """Input for DiscoverRegionActivity."""

    connection_id: str
    workspace_id: str
    snapshot_id: str
    region: str
    resource_kind: str


@dataclass
class DiscoverRegionResult:
    """Result returned by DiscoverRegionActivity."""

    region: str
    resource_kind: str
    status: str  # ok | denied | throttled-partial | error
    count: int = 0
    error: str | None = None


@dataclass
class AWSDiscoveryResult:
    """Final result returned by AWSDiscoveryWorkflow."""

    snapshot_id: str
    status: str  # completed | failed
    coverage: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=4,
    non_retryable_error_types=["AccessDeniedException", "UnauthorizedOperation"],
)


@activity.defn(name="DiscoverRegionActivity")
async def discover_region_activity(inp: DiscoverRegionInput) -> DiscoverRegionResult:
    """Discover all resources of one kind in one region.

    Fetches credentials from OpenBao JIT, calls boto3, normalizes, and upserts
    ResourceRow records. Idempotent on (workspace_id, connection_id, snapshot_id,
    native_id).
    """
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    activity.heartbeat(f"starting {inp.resource_kind} in {inp.region}")

    connection_id = uuid.UUID(inp.connection_id)
    workspace_id = uuid.UUID(inp.workspace_id)
    snapshot_id = uuid.UUID(inp.snapshot_id)

    try:
        from worker_connector.main import get_aws_credentials

        creds = await get_aws_credentials(inp.connection_id)
    except Exception as exc:
        log.error(
            "discovery.activity.cred_fetch_failed",
            connection_id=inp.connection_id,
            region=inp.region,
        )
        raise ApplicationError(str(exc), type="CredentialError") from exc

    try:
        raw_items = await _fetch_raw(creds, inp.region, inp.resource_kind)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("AccessDeniedException", "UnauthorizedOperation",
                          "AuthFailure", "InvalidClientTokenId"):
            return DiscoverRegionResult(
                region=inp.region,
                resource_kind=inp.resource_kind,
                status="denied",
                error=error_code,
            )
        if error_code in ("RequestLimitExceeded", "Throttling"):
            return DiscoverRegionResult(
                region=inp.region,
                resource_kind=inp.resource_kind,
                status="throttled-partial",
                error=error_code,
            )
        raise ApplicationError(str(exc), type="AWSClientError") from exc
    except Exception as exc:
        log.error(
            "discovery.activity.fetch_failed",
            region=inp.region,
            kind=inp.resource_kind,
            error=type(exc).__name__,
        )
        return DiscoverRegionResult(
            region=inp.region,
            resource_kind=inp.resource_kind,
            status="error",
            error=type(exc).__name__,
        )

    activity.heartbeat(f"normalizing {len(raw_items)} {inp.resource_kind} in {inp.region}")

    count = await _normalize_and_upsert(
        raw_items=raw_items,
        resource_kind=inp.resource_kind,
        snapshot_id=snapshot_id,
        connection_id=connection_id,
        workspace_id=workspace_id,
        region=inp.region,
        account=_extract_account(creds, raw_items),
    )

    log.info(
        "discovery.activity.completed",
        region=inp.region,
        kind=inp.resource_kind,
        count=count,
    )
    return DiscoverRegionResult(
        region=inp.region,
        resource_kind=inp.resource_kind,
        status="ok",
        count=count,
    )


# ---------------------------------------------------------------------------
# Helpers used by the activity
# ---------------------------------------------------------------------------


def _build_boto_session(creds: Any, region: str) -> Any:
    import boto3  # type: ignore[import-untyped]

    kwargs: dict[str, Any] = {"region_name": region}
    if creds.access_key_id and creds.secret_access_key:
        kwargs["aws_access_key_id"] = creds.access_key_id
        kwargs["aws_secret_access_key"] = creds.secret_access_key

    base = boto3.Session(**kwargs)

    if creds.role_arn:
        sts = base.client("sts")
        assume_kwargs: dict[str, Any] = {
            "RoleArn": creds.role_arn,
            "RoleSessionName": "aether-migrate-discovery",
        }
        if creds.external_id:
            assume_kwargs["ExternalId"] = creds.external_id
        resp = sts.assume_role(**assume_kwargs)
        tmp = resp["Credentials"]
        return boto3.Session(
            aws_access_key_id=tmp["AccessKeyId"],
            aws_secret_access_key=tmp["SecretAccessKey"],
            aws_session_token=tmp["SessionToken"],
            region_name=region,
        )
    return base


def _paginate_sync(session: Any, service: str, operation: str, result_key: str) -> list[Any]:
    client = session.client(service)
    paginator = client.get_paginator(operation)
    items: list[Any] = []
    for page in paginator.paginate():
        items.extend(page.get(result_key, []))
    return items


async def _fetch_raw(creds: Any, region: str, resource_kind: str) -> list[dict[str, Any]]:
    """Call the appropriate boto3 paginator for *resource_kind* in *region*."""
    loop = asyncio.get_event_loop()

    _KIND_MAP: dict[str, tuple[str, str, str]] = {
        "vm": ("ec2", "describe_instances", "Reservations"),
        "disk": ("ec2", "describe_volumes", "Volumes"),
        "network": ("ec2", "describe_vpcs", "Vpcs"),
        "subnet": ("ec2", "describe_subnets", "Subnets"),
        "security_group": ("ec2", "describe_security_groups", "SecurityGroups"),
        "load_balancer": ("elbv2", "describe_load_balancers", "LoadBalancers"),
    }

    if resource_kind not in _KIND_MAP:
        return []

    service, operation, result_key = _KIND_MAP[resource_kind]

    def _run() -> list[dict[str, Any]]:
        session = _build_boto_session(creds, region)
        items = _paginate_sync(session, service, operation, result_key)
        # Flatten EC2 reservations
        if resource_kind == "vm":
            instances: list[dict[str, Any]] = []
            for reservation in items:
                for inst in reservation.get("Instances", []):
                    inst["_owner_id"] = reservation.get("OwnerId", "")
                    instances.append(inst)
            return instances
        return items

    return await loop.run_in_executor(None, _run)


def _extract_account(creds: Any, raw_items: list[dict[str, Any]]) -> str:
    """Try to extract AWS account ID from raw items or fall back to role ARN."""
    if raw_items:
        owner = raw_items[0].get("_owner_id", "")
        if owner:
            return owner
    if creds.role_arn:
        # ARN format: arn:aws:iam::123456789012:role/name
        parts = creds.role_arn.split(":")
        if len(parts) >= 5:
            return parts[4]
    return ""


async def _normalize_and_upsert(
    raw_items: list[dict[str, Any]],
    resource_kind: str,
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    region: str,
    account: str,
) -> int:
    """Normalize raw items and upsert into ResourceRow. Returns upserted count."""
    from db.engine import get_session
    from db.models import ResourceRow
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from aws.normalizers.disk import normalize_ebs_volume
    from aws.normalizers.network import normalize_subnet, normalize_vpc
    from aws.normalizers.security_group import normalize_security_group
    from aws.normalizers.vm import normalize_ec2_instance

    count = 0
    upsert_rows: list[dict[str, Any]] = []

    for raw in raw_items:
        try:
            if resource_kind == "vm":
                spec, _edges = normalize_ec2_instance(
                    raw, snapshot_id, connection_id, workspace_id,
                    account=account, region=region,
                )
                upsert_rows.append(_spec_to_row(spec, snapshot_id, connection_id, workspace_id, resource_kind, raw))
            elif resource_kind == "disk":
                resource, _edges = normalize_ebs_volume(
                    raw, snapshot_id, connection_id, workspace_id,
                    account=account, region=region,
                )
                upsert_rows.append(_resource_to_row(resource, snapshot_id, connection_id, workspace_id, resource_kind, raw))
            elif resource_kind == "network":
                resource, _edges = normalize_vpc(
                    raw, snapshot_id, connection_id, workspace_id,
                    account=account, region=region,
                )
                upsert_rows.append(_resource_to_row(resource, snapshot_id, connection_id, workspace_id, resource_kind, raw))
            elif resource_kind == "subnet":
                resource, _edges = normalize_subnet(
                    raw, snapshot_id, connection_id, workspace_id,
                    account=account, region=region,
                )
                upsert_rows.append(_resource_to_row(resource, snapshot_id, connection_id, workspace_id, resource_kind, raw))
            elif resource_kind == "security_group":
                resource, rules, _edges = normalize_security_group(
                    raw, snapshot_id, connection_id, workspace_id,
                    account=account, region=region,
                )
                row = _resource_to_row(resource, snapshot_id, connection_id, workspace_id, resource_kind, raw)
                row["spec"]["rules"] = [r.model_dump() for r in rules]
                upsert_rows.append(row)
        except Exception:
            log.exception(
                "discovery.activity.normalize_error",
                kind=resource_kind,
                region=region,
            )
            continue

    if not upsert_rows:
        return 0

    async with get_session() as session:
        stmt = (
            pg_insert(ResourceRow)
            .values(upsert_rows)
            .on_conflict_do_update(
                constraint="uq_resources_native_id",
                set_={
                    "spec": pg_insert(ResourceRow).excluded.spec,
                    "status": pg_insert(ResourceRow).excluded.status,
                    "tags": pg_insert(ResourceRow).excluded.tags,
                    "name": pg_insert(ResourceRow).excluded.name,
                    "discovered_at": pg_insert(ResourceRow).excluded.discovered_at,
                    "provenance": pg_insert(ResourceRow).excluded.provenance,
                },
            )
        )
        await session.execute(stmt)
        count = len(upsert_rows)

    return count


def _spec_to_row(
    spec: Any,
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    kind: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    spec_dict = spec.model_dump(mode="json", exclude={"id", "workspace_id", "connection_id",
                                                       "snapshot_id", "provider", "native_id",
                                                       "account", "region", "zone", "type",
                                                       "name", "status", "tags",
                                                       "created_at_source", "discovered_at",
                                                       "schema_version", "raw_ref"})
    provenance = {
        k: v.model_dump() if hasattr(v, "model_dump") else v
        for k, v in (spec.provenance or {}).items()
    }
    return {
        "id": spec.id,
        "workspace_id": workspace_id,
        "connection_id": connection_id,
        "snapshot_id": snapshot_id,
        "provider": "aws",
        "native_id": spec.native_id,
        "account": spec.account,
        "region": spec.region,
        "zone": spec.zone,
        "kind": kind,
        "name": spec.name,
        "status": spec.status.value if hasattr(spec.status, "value") else str(spec.status),
        "tags": spec.tags,
        "spec": spec_dict,
        "provenance": provenance,
        "raw_ref": None,
        "created_at_source": spec.created_at_source,
        "discovered_at": datetime.now(UTC),
        "schema_version": spec.schema_version,
    }


def _resource_to_row(
    resource: Any,
    snapshot_id: uuid.UUID,
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    kind: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    provenance: dict[str, Any] = {}
    return {
        "id": resource.id,
        "workspace_id": workspace_id,
        "connection_id": connection_id,
        "snapshot_id": snapshot_id,
        "provider": "aws",
        "native_id": resource.native_id,
        "account": resource.account,
        "region": resource.region,
        "zone": resource.zone,
        "kind": kind,
        "name": resource.name,
        "status": resource.status.value if hasattr(resource.status, "value") else str(resource.status),
        "tags": resource.tags,
        "spec": {},
        "provenance": provenance,
        "raw_ref": None,
        "created_at_source": resource.created_at_source,
        "discovered_at": datetime.now(UTC),
        "schema_version": resource.schema_version if hasattr(resource, "schema_version") else "1.0",
    }


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@workflow.defn(name="AWSDiscoveryWorkflow", sandboxed=False)
class AWSDiscoveryWorkflow:
    """Temporal workflow that orchestrates AWS resource discovery.

    Fan-out pattern: one DiscoverRegionActivity per (region × kind) pair.
    A semaphore caps concurrency at DISCOVERY_CONCURRENCY (default 10).
    """

    @workflow.run
    async def run(self, inp: AWSDiscoveryInput) -> AWSDiscoveryResult:
        connection_id = inp.connection_id
        workspace_id = inp.workspace_id
        kinds = inp.resource_kinds or _DEFAULT_KINDS

        # Step 1 — create snapshot row
        snapshot_id_str = await workflow.execute_activity(
            _create_snapshot_activity,
            args=[connection_id, workspace_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        try:
            # Step 2 — resolve regions
            if inp.regions:
                region_names = inp.regions
            else:
                region_names = await workflow.execute_activity(
                    _list_regions_activity,
                    args=[connection_id],
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            # Step 3 — fan out with semaphore
            sem = asyncio.Semaphore(_CONCURRENCY)
            tasks = []
            for region in region_names:
                for kind in kinds:
                    tasks.append(
                        _run_with_semaphore(
                            sem,
                            workflow.execute_activity(
                                discover_region_activity,
                                args=[
                                    DiscoverRegionInput(
                                        connection_id=connection_id,
                                        workspace_id=workspace_id,
                                        snapshot_id=snapshot_id_str,
                                        region=region,
                                        resource_kind=kind,
                                    )
                                ],
                                start_to_close_timeout=timedelta(minutes=30),
                                heartbeat_timeout=timedelta(minutes=5),
                                retry_policy=_RETRY_POLICY,
                            ),
                        )
                    )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Step 4 — aggregate coverage
            coverage: dict[str, str] = {}
            for result in results:
                if isinstance(result, (ActivityError, Exception)):
                    log.warning("workflow.activity_error", error=repr(result))
                    continue
                key = f"{result.region}/{result.resource_kind}"
                coverage[key] = result.status

            await workflow.execute_activity(
                _update_snapshot_activity,
                args=[snapshot_id_str, "completed", coverage],
                start_to_close_timeout=timedelta(seconds=30),
            )

            return AWSDiscoveryResult(
                snapshot_id=snapshot_id_str,
                status="completed",
                coverage=coverage,
            )

        except Exception as exc:
            log.error("workflow.failed", error=repr(exc))
            await workflow.execute_activity(
                _update_snapshot_activity,
                args=[snapshot_id_str, "failed", {}],
                start_to_close_timeout=timedelta(seconds=30),
            )
            raise


async def _run_with_semaphore(sem: asyncio.Semaphore, coro: Any) -> Any:
    async with sem:
        return await coro


# ---------------------------------------------------------------------------
# Snapshot management activities
# ---------------------------------------------------------------------------


@activity.defn(name="CreateSnapshotActivity")
async def _create_snapshot_activity(connection_id: str, workspace_id: str) -> str:
    """Create a SnapshotRow with status=running and return its ID."""
    from db.engine import get_session
    from db.models import SnapshotRow

    snap_id = uuid.uuid4()
    row = SnapshotRow(
        id=snap_id,
        workspace_id=uuid.UUID(workspace_id),
        connection_id=uuid.UUID(connection_id),
        provider="aws",
        status="running",
        coverage={},
    )
    async with get_session() as session:
        session.add(row)

    log.info("discovery.snapshot.created", snapshot_id=str(snap_id))
    return str(snap_id)


@activity.defn(name="UpdateSnapshotActivity")
async def _update_snapshot_activity(
    snapshot_id: str, status: str, coverage: dict[str, str]
) -> None:
    """Update snapshot status and coverage report."""
    from db.engine import get_session
    from db.models import SnapshotRow
    from sqlalchemy import select

    async with get_session() as session:
        result = await session.execute(
            select(SnapshotRow).where(SnapshotRow.id == uuid.UUID(snapshot_id))
        )
        row = result.scalar_one_or_none()
        if row is None:
            log.warning("discovery.snapshot.not_found", snapshot_id=snapshot_id)
            return
        row.status = status
        row.coverage = coverage
        row.completed_at = datetime.now(UTC)

    log.info("discovery.snapshot.updated", snapshot_id=snapshot_id, status=status)


@activity.defn(name="ListRegionsActivity")
async def _list_regions_activity(connection_id: str) -> list[str]:
    """List enabled AWS regions for the connection."""
    from aws.regions import list_aws_regions

    try:
        from worker_connector.main import get_aws_credentials

        creds = await get_aws_credentials(connection_id)

        import asyncio


        def _describe() -> list[str]:
            import boto3  # type: ignore[import-untyped]

            s_kwargs: dict[str, Any] = {"region_name": creds.default_region}
            if creds.access_key_id and creds.secret_access_key:
                s_kwargs["aws_access_key_id"] = creds.access_key_id
                s_kwargs["aws_secret_access_key"] = creds.secret_access_key
            session = boto3.Session(**s_kwargs)
            ec2 = session.client("ec2", region_name=creds.default_region)
            resp = ec2.describe_regions(
                Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}]
            )
            return [r["RegionName"] for r in resp.get("Regions", [])]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _describe)

    except Exception:
        log.warning("discovery.list_regions.fallback", connection_id=connection_id)
        return list_aws_regions()
