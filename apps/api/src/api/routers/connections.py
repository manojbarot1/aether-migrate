"""Connections router — full CRUD + test for cloud provider connections.

Credential flow:
1. ``POST /connections`` — stores credential in OpenBao under
   ``cloud-creds/{workspace_id}/{connection_id}``, then saves metadata-only
   row in Postgres (no secret values in DB).
2. ``POST /connections/{id}/test`` — reads creds from OpenBao, runs provider
   test, updates ``last_tested_at`` / ``last_test_ok``.
3. ``DELETE /connections/{id}`` — deletes from OpenBao first, then removes DB row.

All mutating endpoints record an audit event.
Credential values are NEVER returned in any response or logged.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role
from api.schemas.connections import (
    ConnectionCreateRequest,
    ConnectionResponse,
    ConnectionTestResult,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/connections", tags=["connections"])


# ---------------------------------------------------------------------------
# OpenBao dependency
# ---------------------------------------------------------------------------


def _get_bao_client() -> Any:
    """Return a configured OpenBaoClient.  Imported lazily to keep api package
    free of a hard import-time dependency on secrets_svc."""
    from secrets_svc import OpenBaoClient

    return OpenBaoClient()


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _record_audit(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user: AuthenticatedUser,
    action: str,
    target_id: str,
    connection_id: uuid.UUID | None = None,
    result_status: str = "success",
) -> None:
    from audit.models import AuditEvent
    from audit.writer import AuditWriter

    event = AuditEvent(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=action,
        target_type="Connection",
        target_id=target_id,
        connection_id=connection_id,
        result_status=result_status,
    )
    writer = AuditWriter(workspace_id=workspace_id, session_factory=lambda: session)
    await writer.record(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_connection_or_404(
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    session: AsyncSession,
) -> Any:
    from db.models import ConnectionRow

    result = await session.execute(
        select(ConnectionRow).where(
            ConnectionRow.id == connection_id,
            ConnectionRow.workspace_id == workspace_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return row


def _bao_path(workspace_id: uuid.UUID, connection_id: uuid.UUID) -> str:
    return f"{workspace_id}/{connection_id}"


def _build_bao_payload(body: ConnectionCreateRequest) -> dict[str, Any]:
    """Assemble the credential dict that goes into OpenBao.

    Only the fields with actual values are included to keep the stored
    secret minimal.  This dict is stored in OpenBao, NEVER in Postgres.
    """
    payload: dict[str, Any] = {
        "provider": body.provider.value if hasattr(body.provider, "value") else body.provider,
        "aws_default_region": body.aws_default_region,
    }
    if body.aws_role_arn:
        payload["aws_role_arn"] = body.aws_role_arn
    if body.aws_external_id:
        payload["aws_external_id"] = body.aws_external_id
    if body.aws_access_key_id:
        payload["aws_access_key_id"] = body.aws_access_key_id
    if body.aws_secret_access_key:
        payload["aws_secret_access_key"] = body.aws_secret_access_key
    return payload


def _build_metadata(body: ConnectionCreateRequest) -> dict[str, Any]:
    """Build the metadata-only JSONB blob that is safe to store in Postgres.

    Contains no credential values — only structural flags.
    """
    return {
        "provider": body.provider.value if hasattr(body.provider, "value") else body.provider,
        "default_region": body.aws_default_region,
        "has_role_arn": body.aws_role_arn is not None,
        "has_access_key": body.aws_access_key_id is not None,
    }


def _row_to_response(row: Any) -> ConnectionResponse:
    return ConnectionResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        provider=row.provider,
        mode=row.mode,
        scope=row.scope,
        last_tested_at=row.last_tested_at,
        last_test_ok=row.last_test_ok,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ConnectionResponse, status_code=201)
async def create_connection(
    body: ConnectionCreateRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("connection-admin")),
) -> ConnectionResponse:
    """Create a new connection: store credentials in OpenBao, metadata in DB."""
    from db.models import ConnectionRow

    connection_id = uuid.uuid4()
    bao = _get_bao_client()

    try:
        # 1. Store credentials in OpenBao (path only logged, not values)
        bao_path = _bao_path(workspace_id, connection_id)
        await bao.write_credential(bao_path, _build_bao_payload(body))
    except Exception as exc:
        log.error("connection.create.bao_write_failed", connection_id=str(connection_id))
        raise HTTPException(status_code=502, detail="Failed to store credentials") from exc
    finally:
        await bao.aclose()

    # 2. Persist metadata-only row — NO secrets
    row = ConnectionRow(
        id=connection_id,
        workspace_id=workspace_id,
        name=body.name,
        provider=body.provider.value if hasattr(body.provider, "value") else body.provider,
        mode=body.mode,
        scope=body.scope,
        metadata_=_build_metadata(body),
    )
    session.add(row)
    await session.flush()

    # 3. Audit
    await _record_audit(session, workspace_id, user, "connection.create", str(connection_id), connection_id)

    log.info("connection.created", connection_id=str(connection_id), provider=str(body.provider))
    return _row_to_response(row)


@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> list[ConnectionResponse]:
    """List all connections in the workspace (metadata only, no credentials)."""
    from db.models import ConnectionRow

    result = await session.execute(
        select(ConnectionRow).where(ConnectionRow.workspace_id == workspace_id)
    )
    rows = result.scalars().all()
    return [_row_to_response(r) for r in rows]


@router.get("/aws/policy")
async def get_aws_policy() -> dict[str, Any]:
    """Return the minimal IAM policy JSON for discovery and a setup guide.

    No authentication required — this is public documentation.
    """
    from aws.policy_generator import generate_assume_role_trust_policy, generate_discovery_policy

    platform_account = os.environ.get("AWS_PLATFORM_ACCOUNT_ID", "REPLACE_WITH_PLATFORM_ACCOUNT")
    example_external_id = "aether-migrate-REPLACE_WITH_WORKSPACE_ID"

    return {
        "discovery_policy": generate_discovery_policy(),
        "trust_policy_example": generate_assume_role_trust_policy(
            external_id=example_external_id,
            platform_account_id=platform_account,
        ),
        "setup_guide": (
            "1. In the target AWS account, create an IAM role named "
            "'AetherMigrateDiscovery'.\n"
            "2. Attach the discovery_policy above as an inline or managed policy.\n"
            "3. Set the trust policy to trust_policy_example above, replacing "
            "REPLACE_WITH_WORKSPACE_ID with your Aether workspace ID.\n"
            "4. Copy the role ARN and create a connection via POST /connections "
            "with aws_role_arn set to that ARN."
        ),
    }


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> ConnectionResponse:
    """Get a single connection by ID (metadata only, no credentials)."""
    row = await _get_connection_or_404(connection_id, workspace_id, session)
    return _row_to_response(row)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("connection-admin")),
) -> None:
    """Soft-delete: remove credentials from OpenBao, then delete the DB row."""
    row = await _get_connection_or_404(connection_id, workspace_id, session)
    bao = _get_bao_client()

    try:
        bao_path = _bao_path(workspace_id, connection_id)
        await bao.delete_credential(bao_path)
    except Exception:
        # Log but do not block deletion — credential may already be absent
        log.warning("connection.delete.bao_delete_failed", connection_id=str(connection_id))
    finally:
        await bao.aclose()

    await _record_audit(session, workspace_id, user, "connection.delete", str(connection_id), connection_id)
    await session.delete(row)
    log.info("connection.deleted", connection_id=str(connection_id))


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> ConnectionTestResult:
    """Run a live connection test and record the result."""
    row = await _get_connection_or_404(connection_id, workspace_id, session)

    # Fetch credentials from OpenBao just-in-time
    bao = _get_bao_client()
    try:
        bao_path = _bao_path(workspace_id, connection_id)
        cred_data = await bao.read_credential(bao_path)
        log.info("connection.test.fetched_credentials", connection_id=str(connection_id))
    except Exception as exc:
        log.error("connection.test.bao_read_failed", connection_id=str(connection_id))
        await _record_audit(
            session, workspace_id, user, "connection.test",
            str(connection_id), connection_id, result_status="failure",
        )
        raise HTTPException(status_code=502, detail="Failed to read credentials") from exc
    finally:
        await bao.aclose()

    # Delegate to the AWS connection tester
    try:
        from aws.connection_test import AWSConnectionTester, AWSCredentials
    except ImportError as exc:
        raise HTTPException(
            status_code=501, detail="Provider tester not available"
        ) from exc

    creds = AWSCredentials(
        role_arn=cred_data.get("aws_role_arn"),
        external_id=cred_data.get("aws_external_id"),
        access_key_id=cred_data.get("aws_access_key_id"),
        secret_access_key=cred_data.get("aws_secret_access_key"),
        default_region=cred_data.get("aws_default_region", "us-east-1"),
    )

    tester = AWSConnectionTester()
    result = await tester.test(creds)

    # Persist result
    row.last_tested_at = datetime.now(tz=UTC)
    row.last_test_ok = result.ok
    await session.flush()

    await _record_audit(
        session, workspace_id, user, "connection.test",
        str(connection_id), connection_id,
        result_status="success" if result.ok else "failure",
    )
    log.info("connection.tested", connection_id=str(connection_id), ok=result.ok)
    return result
