"""AWS provider adapter — full Phase 2a implementation.

Uses boto3 via ``asyncio.run_in_executor`` to keep the async event loop
unblocked. Each region is processed with an ``asyncio.Semaphore`` to cap
concurrent boto3 calls per connection.

Environment variables:
    DISCOVERY_CONCURRENCY: max concurrent boto3 calls (default 10)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from core.models import (
    NormalizedBundle,
    ProviderName,
    Region,
    ResourceEdge,
    ResourceKind,
)
from providers_base.adapter import (
    AdapterCapabilities,
    CatalogBatch,
    ConnCtx,
    ConnectionTestResult,
    MetricSeries,
    ProviderAdapter,
    QuotaNeeds,
    QuotaResult,
    RawResource,
    Window,
)

log = structlog.get_logger(__name__)

_DEFAULT_CONCURRENCY = int(os.environ.get("DISCOVERY_CONCURRENCY", "10"))


class AWSAdapter(ProviderAdapter):
    """AWS cloud provider adapter — Phase 2a implementation."""

    provider: ProviderName = ProviderName.aws
    capabilities: AdapterCapabilities = AdapterCapabilities(
        can_discover=True,
        can_fetch_metrics=True,
        can_sync_catalog=True,
        can_check_quotas=True,
        can_execute_changes=False,  # Phase 5
        supported_kinds={
            ResourceKind.vm,
            ResourceKind.disk,
            ResourceKind.nic,
            ResourceKind.network,
            ResourceKind.subnet,
            ResourceKind.security_group,
            ResourceKind.load_balancer,
        },
    )

    def __init__(self, concurrency: int = _DEFAULT_CONCURRENCY) -> None:
        self._sem = asyncio.Semaphore(concurrency)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _build_boto_session(self, creds: Any) -> Any:
        """Create a boto3 Session from an AWSCredentials object."""
        import boto3  # type: ignore[import-untyped]

        session_kwargs: dict[str, Any] = {
            "region_name": creds.default_region,
        }
        if creds.access_key_id and creds.secret_access_key:
            session_kwargs["aws_access_key_id"] = creds.access_key_id
            session_kwargs["aws_secret_access_key"] = creds.secret_access_key

        base_session = boto3.Session(**session_kwargs)

        if creds.role_arn:
            sts = base_session.client("sts")
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
                region_name=creds.default_region,
            )

        return base_session

    async def _run_sync(self, fn: Any, *args: Any) -> Any:
        """Run a synchronous callable in the default executor under the semaphore."""
        loop = asyncio.get_event_loop()
        async with self._sem:
            return await loop.run_in_executor(None, fn, *args)

    async def _get_creds(self, connection_id: uuid.UUID) -> Any:
        """Fetch AWSCredentials from OpenBao via worker_connector helper."""
        from worker_connector.main import get_aws_credentials

        return await get_aws_credentials(str(connection_id))

    # -------------------------------------------------------------------------
    # test_connection
    # -------------------------------------------------------------------------

    async def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult:
        """Validate credentials using STS get_caller_identity."""
        from aws.connection_test import AWSConnectionTester

        creds = await self._get_creds(ctx.connection_id)
        tester = AWSConnectionTester()
        result = await tester.test(creds)
        return result

    # -------------------------------------------------------------------------
    # list_regions
    # -------------------------------------------------------------------------

    async def list_regions(self, ctx: ConnCtx) -> list[Region]:
        """Return enabled regions via ec2.describe_regions.

        Falls back to the static list in ``aws.regions`` if the API call fails.
        """
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

        from aws.regions import list_aws_regions

        creds = await self._get_creds(ctx.connection_id)

        def _describe() -> list[str]:
            session = self._build_boto_session(creds)
            ec2 = session.client("ec2", region_name=creds.default_region)
            resp = ec2.describe_regions(
                Filters=[
                    {
                        "Name": "opt-in-status",
                        "Values": ["opt-in-not-required", "opted-in"],
                    }
                ]
            )
            return [r["RegionName"] for r in resp.get("Regions", [])]

        try:
            region_names = await self._run_sync(_describe)
        except (ClientError, BotoCoreError) as exc:
            log.warning(
                "aws.adapter.list_regions_fallback",
                connection_id=str(ctx.connection_id),
                error=type(exc).__name__,
            )
            region_names = list_aws_regions()

        return [
            Region(
                provider=ProviderName.aws,
                name=name,
                display_name=name,
            )
            for name in region_names
        ]

    # -------------------------------------------------------------------------
    # discover
    # -------------------------------------------------------------------------

    async def _paginate(
        self,
        session: Any,
        region_name: str,
        service: str,
        operation: str,
        result_key: str,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Paginate a boto3 operation and return all items."""
        def _fetch() -> list[dict[str, Any]]:
            client = session.client(service, region_name=region_name)
            paginator = client.get_paginator(operation)
            items: list[dict[str, Any]] = []
            for page in paginator.paginate(**extra_kwargs):
                items.extend(page.get(result_key, []))
            return items

        return await self._run_sync(_fetch)

    async def _discover_ec2(
        self,
        session: Any,
        region: str,
        account: str,
        snapshot_id: uuid.UUID,
        connection_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> list[RawResource]:
        """Fetch all EC2 instances in *region*."""
        reservations = await self._paginate(
            session, region, "ec2", "describe_instances",
            "Reservations",
        )
        raw_resources: list[RawResource] = []
        for reservation in reservations:
            owner_id = reservation.get("OwnerId", account)
            for instance in reservation.get("Instances", []):
                raw_resources.append(
                    RawResource(
                        provider=ProviderName.aws,
                        kind=ResourceKind.vm,
                        native_id=instance["InstanceId"],
                        region=region,
                        account=owner_id,
                        data=instance,
                    )
                )
        return raw_resources

    async def _discover_ebs(
        self, session: Any, region: str, account: str,
    ) -> list[RawResource]:
        volumes = await self._paginate(
            session, region, "ec2", "describe_volumes", "Volumes",
        )
        return [
            RawResource(
                provider=ProviderName.aws,
                kind=ResourceKind.disk,
                native_id=v["VolumeId"],
                region=region,
                account=account,
                data=v,
            )
            for v in volumes
        ]

    async def _discover_vpcs(
        self, session: Any, region: str, account: str,
    ) -> list[RawResource]:
        vpcs = await self._paginate(
            session, region, "ec2", "describe_vpcs", "Vpcs",
        )
        return [
            RawResource(
                provider=ProviderName.aws,
                kind=ResourceKind.network,
                native_id=v["VpcId"],
                region=region,
                account=account,
                data=v,
            )
            for v in vpcs
        ]

    async def _discover_subnets(
        self, session: Any, region: str, account: str,
    ) -> list[RawResource]:
        subnets = await self._paginate(
            session, region, "ec2", "describe_subnets", "Subnets",
        )
        return [
            RawResource(
                provider=ProviderName.aws,
                kind=ResourceKind.subnet,
                native_id=s["SubnetId"],
                region=region,
                account=account,
                data=s,
            )
            for s in subnets
        ]

    async def _discover_security_groups(
        self, session: Any, region: str, account: str,
    ) -> list[RawResource]:
        sgs = await self._paginate(
            session, region, "ec2", "describe_security_groups", "SecurityGroups",
        )
        return [
            RawResource(
                provider=ProviderName.aws,
                kind=ResourceKind.security_group,
                native_id=sg["GroupId"],
                region=region,
                account=account,
                data=sg,
            )
            for sg in sgs
        ]

    async def _discover_load_balancers(
        self, session: Any, region: str, account: str,
    ) -> list[RawResource]:
        lbs = await self._paginate(
            session, region, "elbv2", "describe_load_balancers", "LoadBalancers",
        )
        return [
            RawResource(
                provider=ProviderName.aws,
                kind=ResourceKind.load_balancer,
                native_id=lb["LoadBalancerArn"],
                region=region,
                account=account,
                data=lb,
            )
            for lb in lbs
        ]

    async def discover(  # type: ignore[override]
        self,
        ctx: ConnCtx,
        region: Region,
        kinds: set[ResourceKind],
    ) -> AsyncIterator[RawResource]:
        """Stream raw resources from the provider API."""
        creds = await self._get_creds(ctx.connection_id)
        session = self._build_boto_session(creds)
        account = ctx.account
        region_name = region.name

        fetchers: list[Any] = []
        if ResourceKind.vm in kinds:
            fetchers.append(
                self._discover_ec2(
                    session, region_name, account,
                    uuid.UUID(str(ctx.extra.get("snapshot_id", uuid.uuid4()))),
                    ctx.connection_id, ctx.workspace_id,
                )
            )
        if ResourceKind.disk in kinds:
            fetchers.append(self._discover_ebs(session, region_name, account))
        if ResourceKind.network in kinds:
            fetchers.append(self._discover_vpcs(session, region_name, account))
        if ResourceKind.subnet in kinds:
            fetchers.append(self._discover_subnets(session, region_name, account))
        if ResourceKind.security_group in kinds:
            fetchers.append(self._discover_security_groups(session, region_name, account))
        if ResourceKind.load_balancer in kinds:
            fetchers.append(self._discover_load_balancers(session, region_name, account))

        results = await asyncio.gather(*fetchers, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.warning(
                    "aws.adapter.discover_partial_error",
                    region=region_name,
                    error=type(result).__name__,
                )
                continue
            for raw in result:
                yield raw

    # -------------------------------------------------------------------------
    # normalize
    # -------------------------------------------------------------------------

    def normalize(self, raw: RawResource) -> NormalizedBundle:
        """Dispatch to the appropriate normalizer based on resource kind."""
        from aws.normalizers.disk import normalize_ebs_volume
        from aws.normalizers.network import normalize_subnet, normalize_vpc
        from aws.normalizers.security_group import normalize_security_group
        from aws.normalizers.vm import normalize_ec2_instance

        # These are placeholders; the workflow passes real UUIDs via extra
        snapshot_id = uuid.UUID(str(raw.data.get("_snapshot_id", uuid.uuid4())))
        connection_id = uuid.UUID(str(raw.data.get("_connection_id", uuid.uuid4())))
        workspace_id = uuid.UUID(str(raw.data.get("_workspace_id", uuid.uuid4())))

        resources = []
        edges: list[ResourceEdge] = []

        if raw.kind == ResourceKind.vm:
            spec, vm_edges = normalize_ec2_instance(
                raw.data, snapshot_id, connection_id, workspace_id,
                account=raw.account, region=raw.region,
            )
            resources.append(spec)
            edges.extend(vm_edges)

        elif raw.kind == ResourceKind.disk:
            resource, disk_edges = normalize_ebs_volume(
                raw.data, snapshot_id, connection_id, workspace_id,
                account=raw.account, region=raw.region,
            )
            resources.append(resource)
            edges.extend(disk_edges)

        elif raw.kind == ResourceKind.network:
            resource, net_edges = normalize_vpc(
                raw.data, snapshot_id, connection_id, workspace_id,
                account=raw.account, region=raw.region,
            )
            resources.append(resource)
            edges.extend(net_edges)

        elif raw.kind == ResourceKind.subnet:
            resource, subnet_edges = normalize_subnet(
                raw.data, snapshot_id, connection_id, workspace_id,
                account=raw.account, region=raw.region,
            )
            resources.append(resource)
            edges.extend(subnet_edges)

        elif raw.kind == ResourceKind.security_group:
            resource, _rules, sg_edges = normalize_security_group(
                raw.data, snapshot_id, connection_id, workspace_id,
                account=raw.account, region=raw.region,
            )
            resources.append(resource)
            edges.extend(sg_edges)

        return NormalizedBundle(resources=resources, edges=edges)

    # -------------------------------------------------------------------------
    # Unimplemented methods (Phase 2b / 3)
    # -------------------------------------------------------------------------

    async def fetch_metrics(
        self, ctx: ConnCtx, ids: list[str], window: Window
    ) -> list[MetricSeries]:
        raise NotImplementedError("AWSAdapter.fetch_metrics: Phase 2b")

    async def sync_catalog(self, regions: list[Region]) -> CatalogBatch:
        raise NotImplementedError("AWSAdapter.sync_catalog: Phase 2b")

    async def check_quotas(
        self, ctx: ConnCtx, region: Region, needs: QuotaNeeds
    ) -> list[QuotaResult]:
        raise NotImplementedError("AWSAdapter.check_quotas: Phase 3")
