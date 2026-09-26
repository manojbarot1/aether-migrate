from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from temporalio.client import Client, WorkflowFailureError

from aether.api.deps import (
    SettingsDep,
    WorkspaceContext,
    get_bao,
    get_temporal,
    request_id,
    require_role,
)
from aether.api.schemas import ConnectionCreate, ConnectionOut, ConnectionUpdate, PolicyTemplate
from aether.audit.writer import AuditRecord, record
from aether.core.connections import AwsAccessKeyConfig, AwsAssumeRoleConfig
from aether.core.enums import AuditStatus, AuthMethod, ConnectionMode, ConnectionStatus, Provider, Role
from aether.core.errors import ConflictError, NotFoundError, UpstreamError, ValidationFailedError
from aether.core.policies import aws_permissions_policy, aws_trust_policy
from aether.db.models import CloudConnection
from aether.db.session import workspace_scope
from aether.secrets.openbao import OpenBaoClient
from aether.workflows.connection_test import TestConnectionInput, TestConnectionWorkflow

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/connections", tags=["connections"])

Viewer = Annotated[WorkspaceContext, Depends(require_role(Role.VIEWER))]
ConnAdmin = Annotated[WorkspaceContext, Depends(require_role(Role.CONNECTION_ADMIN))]
Bao = Annotated[OpenBaoClient, Depends(get_bao)]
Temporal = Annotated[Client, Depends(get_temporal)]
RequestId = Annotated[str | None, Depends(request_id)]

SUPPORTED_PROVIDERS = {Provider.AWS}


def secret_path(workspace_id: uuid.UUID, connection_id: uuid.UUID) -> str:
    return f"ws/{workspace_id}/conn/{connection_id}"


def to_out(c: CloudConnection) -> ConnectionOut:
    return ConnectionOut(
        id=c.id,
        workspace_id=c.workspace_id,
        name=c.name,
        provider=Provider(c.provider),
        mode=ConnectionMode(c.mode),
        auth_method=c.auth_method,
        config=c.config,
        has_secret=c.secret_path is not None,
        secret_version=c.secret_version,
        status=c.status,
        last_tested_at=c.last_tested_at,
        last_test_result=c.last_test_result,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def _get(ctx: WorkspaceContext, connection_id: uuid.UUID) -> CloudConnection:
    # RLS already restricts rows to this workspace; the explicit filter is belt and braces.
    c = (
        await ctx.session.execute(
            select(CloudConnection).where(
                CloudConnection.id == connection_id, CloudConnection.workspace_id == ctx.workspace_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise NotFoundError("connection not found")
    return c


@router.get("", response_model=list[ConnectionOut])
async def list_connections(ctx: Viewer) -> list[ConnectionOut]:
    rows = (
        await ctx.session.execute(
            select(CloudConnection)
            .where(CloudConnection.workspace_id == ctx.workspace_id)
            .order_by(CloudConnection.name)
        )
    ).scalars()
    return [to_out(c) for c in rows]


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(connection_id: uuid.UUID, ctx: Viewer) -> ConnectionOut:
    return to_out(await _get(ctx, connection_id))


@router.post("", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreate, ctx: ConnAdmin, bao: Bao, rid: RequestId
) -> ConnectionOut:
    if body.provider not in SUPPORTED_PROVIDERS:
        raise ValidationFailedError(f"provider '{body.provider.value}' is not supported yet")
    if body.mode != ConnectionMode.READ_ONLY:
        raise ValidationFailedError("execution connections are not available yet (roadmap phase 9)")

    cfg = body.config
    if isinstance(cfg, AwsAssumeRoleConfig):
        if body.secret is not None:
            raise ValidationFailedError("assume-role connections must not include a secret")
        # Platform-generated ExternalId (confused-deputy protection).
        cfg = cfg.model_copy(update={"external_id": f"aether-{secrets.token_urlsafe(24)}"})
    elif isinstance(cfg, AwsAccessKeyConfig) and body.secret is None:
        raise ValidationFailedError("access-key connections require a secret")

    conn = CloudConnection(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        name=body.name,
        provider=body.provider.value,
        mode=body.mode.value,
        auth_method=cfg.auth_method.value,
        config=cfg.model_dump(mode="json", exclude={"auth_method"}),
        status=ConnectionStatus.UNTESTED.value,
        created_by=ctx.principal.user_id,
    )
    ctx.session.add(conn)
    try:
        await ctx.session.flush()
    except IntegrityError:
        raise ConflictError("a connection with this name already exists") from None

    if body.secret is not None:
        path = secret_path(ctx.workspace_id, conn.id)
        conn.secret_version = await bao.kv_write(
            path,
            {
                "access_key_id": body.secret.access_key_id.get_secret_value(),
                "secret_access_key": body.secret.secret_access_key.get_secret_value(),
            },
        )
        conn.secret_path = path

    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="connection.create",
            workspace_id=ctx.workspace_id,
            connection_id=conn.id,
            target_type="connection",
            target_id=str(conn.id),
            details={
                "name": conn.name,
                "provider": conn.provider,
                "mode": conn.mode,
                "auth_method": conn.auth_method,
                "config": conn.config,
                "secret_stored": conn.secret_path is not None,
            },
            request_id=rid,
        ),
    )
    await ctx.session.flush()
    await ctx.session.refresh(conn)
    return to_out(conn)


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID, body: ConnectionUpdate, ctx: ConnAdmin, bao: Bao, rid: RequestId
) -> ConnectionOut:
    conn = await _get(ctx, connection_id)
    changes: dict[str, object] = {}
    if body.name is not None and body.name != conn.name:
        changes["name"] = {"from": conn.name, "to": body.name}
        conn.name = body.name
    if body.regions is not None:
        model = AwsAssumeRoleConfig if conn.auth_method == AuthMethod.AWS_ASSUME_ROLE else AwsAccessKeyConfig
        validated = model.model_validate({**conn.config, "regions": body.regions})
        changes["regions"] = {"from": conn.config.get("regions"), "to": validated.regions}
        conn.config = {**conn.config, "regions": validated.regions}
    if body.secret is not None:
        if conn.auth_method != AuthMethod.AWS_ACCESS_KEY:
            raise ValidationFailedError("this connection does not use a stored secret")
        path = conn.secret_path or secret_path(ctx.workspace_id, conn.id)
        conn.secret_version = await bao.kv_write(
            path,
            {
                "access_key_id": body.secret.access_key_id.get_secret_value(),
                "secret_access_key": body.secret.secret_access_key.get_secret_value(),
            },
        )
        conn.secret_path = path
        changes["secret"] = "rotated"
    if changes:
        # Any change invalidates the previous test result.
        conn.status = ConnectionStatus.UNTESTED.value
        try:
            await ctx.session.flush()
        except IntegrityError:
            raise ConflictError("a connection with this name already exists") from None
        await record(
            ctx.session,
            AuditRecord(
                actor=ctx.principal.actor,
                action="connection.update",
                workspace_id=ctx.workspace_id,
                connection_id=conn.id,
                target_type="connection",
                target_id=str(conn.id),
                details=changes,
                request_id=rid,
            ),
        )
        await ctx.session.flush()
        await ctx.session.refresh(conn)
    return to_out(conn)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: uuid.UUID, ctx: ConnAdmin, bao: Bao, rid: RequestId) -> None:
    conn = await _get(ctx, connection_id)
    if conn.secret_path:
        await bao.kv_destroy_all(conn.secret_path)
    await ctx.session.delete(conn)
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="connection.delete",
            workspace_id=ctx.workspace_id,
            connection_id=conn.id,
            target_type="connection",
            target_id=str(conn.id),
            details={
                "name": conn.name,
                "provider": conn.provider,
                "secret_destroyed": bool(conn.secret_path),
            },
            request_id=rid,
        ),
    )


@router.get("/{connection_id}/setup", response_model=PolicyTemplate)
async def connection_setup(connection_id: uuid.UUID, ctx: Viewer, settings: SettingsDep) -> PolicyTemplate:
    """Least-privilege IAM policy (and trust policy, for assume-role) to create in the customer account."""
    conn = await _get(ctx, connection_id)
    if conn.auth_method == AuthMethod.AWS_ASSUME_ROLE:
        external_id = conn.config["external_id"]
        return PolicyTemplate(
            provider=Provider.AWS,
            auth_method=conn.auth_method,
            external_id=external_id,
            trust_policy=aws_trust_policy(settings.aws_platform_principal_arn, external_id),
            permissions_policy=aws_permissions_policy(),
            instructions=[
                "Create an IAM role with the trust policy below (it only trusts the platform identity "
                "and requires this connection's ExternalId).",
                "Attach the permissions policy as an inline or customer-managed policy; replace ROLE_NAME "
                "with the role's name.",
                "Do not attach AWS managed policies such as ReadOnlyAccess: "
                "they grant more than discovery needs.",
                f"Make sure the role ARN matches {conn.config['role_arn']}, then run the connection test.",
            ],
        )
    return PolicyTemplate(
        provider=Provider.AWS,
        auth_method=conn.auth_method,
        external_id=None,
        trust_policy=None,
        permissions_policy=aws_permissions_policy(),
        instructions=[
            "Prefer an assume-role connection: access keys are long-lived.",
            "Create a dedicated IAM user used only by this platform and attach the permissions policy below "
            "(replace the self-simulation resource with arn:aws:iam::ACCOUNT_ID:user/USER_NAME).",
            "Rotate the key regularly; update it here with 'Rotate secret'.",
        ],
    )


@router.post("/{connection_id}/test", response_model=ConnectionOut)
async def test_connection(
    connection_id: uuid.UUID, ctx: ConnAdmin, temporal: Temporal, settings: SettingsDep, rid: RequestId
) -> ConnectionOut:
    conn = await _get(ctx, connection_id)
    inp = TestConnectionInput(
        workspace_id=str(ctx.workspace_id),
        connection_id=str(conn.id),
        requested_by=str(ctx.principal.user_id),
        requested_by_display=ctx.principal.actor.display,
        request_id=rid,
    )
    # Commit the request's transaction before waiting: the connector worker updates
    # the same row, and holding a transaction open across the wait would block it.
    await ctx.session.commit()
    try:
        await temporal.execute_workflow(
            TestConnectionWorkflow.run,
            inp,
            id=f"conn-test-{conn.id}-{uuid.uuid4().hex[:8]}",
            task_queue=settings.connector_task_queue,
            execution_timeout=timedelta(minutes=5),
        )
    except (TimeoutError, WorkflowFailureError) as e:
        async with workspace_scope(ctx.workspace_id) as s:
            await record(
                s,
                AuditRecord(
                    actor=ctx.principal.actor,
                    action="connection.test",
                    status=AuditStatus.FAILURE,
                    workspace_id=ctx.workspace_id,
                    connection_id=conn.id,
                    target_type="connection",
                    target_id=str(conn.id),
                    details={"error": type(e).__name__},
                    request_id=rid,
                ),
            )
        raise UpstreamError("connection test could not be completed; see the audit log") from e

    async with workspace_scope(ctx.workspace_id) as s:
        fresh = await s.get(CloudConnection, conn.id)
        if fresh is None:
            raise NotFoundError("connection not found")
        return to_out(fresh)
