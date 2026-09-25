"""Integration tests for the plans API router.

Uses direct endpoint function calls (same pattern as test_assessment_api.py).

Tests:
- POST /plans — creates a plan and returns it
- GET /plans/{id} — retrieves full document
- GET /plans/{id}/export/json — downloads JSON
- POST /plans/{id}/approve — approval enforcement
- Self-approval rejection
- Approved plan immutability (double-approve returns 409)
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from db.models import (
    Base,
    CatalogInstanceTypeRow,
    ConnectionRow,
    ResourceRow,
    SnapshotRow,
    UserRow,
    WorkspaceRow,
)
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("1111aaaa-0000-0000-0000-000000000001")
CREATOR_ID = uuid.UUID("2222bbbb-0000-0000-0000-000000000001")
APPROVER_ID = uuid.UUID("3333cccc-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("4444dddd-0000-0000-0000-000000000001")
SNAPSHOT_ID = uuid.UUID("5555eeee-0000-0000-0000-000000000001")
VM_ID = uuid.UUID("6666ffff-0000-0000-0000-000000000001")


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    eff = datetime(2025, 1, 1)
    async with _session_factory() as db:
        db.add(WorkspaceRow(id=WORKSPACE_ID, name="plans-test", slug="plans-test", settings={}))
        db.add(UserRow(id=CREATOR_ID, workspace_id=WORKSPACE_ID, email="creator@test.com",
                       name="Creator", role="analyst"))
        db.add(UserRow(id=APPROVER_ID, workspace_id=WORKSPACE_ID, email="approver@test.com",
                       name="Approver", role="approver"))
        db.add(ConnectionRow(id=CONNECTION_ID, workspace_id=WORKSPACE_ID, provider="aws",
                             name="test-conn", mode="read-only", metadata_={}))
        db.add(SnapshotRow(id=SNAPSHOT_ID, workspace_id=WORKSPACE_ID,
                           connection_id=CONNECTION_ID, provider="aws",
                           status="completed",
                           completed_at=datetime(2025, 1, 1)))
        db.add(ResourceRow(
            id=VM_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            snapshot_id=SNAPSHOT_ID,
            provider="aws",
            native_id="i-plans-test",
            account="123456789",
            region="us-east-1",
            kind="vm",
            name="plans-vm",
            status="running",
            tags={},
            spec={
                "vcpu": 4,
                "memory_gib": 16.0,
                "architecture": "x86_64",
                "instance_type": "m5.xlarge",
                "os_name": "Ubuntu",
                "os_version": "22.04",
                "disks": [{"size_gib": 50.0, "type_class": "gp3", "boot": True}],
                "nics": [{"private_ips": ["10.0.0.1"], "public_ips": [], "security_group_ids": []}],
            },
            provenance={},
        ))
        db.add(CatalogInstanceTypeRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version="2025-01",
            effective_from=eff,
            region="eastus",
            sku="Standard_D4s_v5",
            vcpu=4,
            memory_mib=16384,
            cpu_arch="x86_64",
            local_nvme_gib=0,
            os_support={"linux": True, "windows": True},
            available_in_region=True,
            restricted=False,
        ))
        await db.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _creator_user():
    from api.auth import AuthenticatedUser
    return AuthenticatedUser(
        user_id=CREATOR_ID,
        email="creator@test.com",
        workspace_id=WORKSPACE_ID,
        roles=["analyst"],
        token_kind="api_token",
    )


def _approver_user():
    from api.auth import AuthenticatedUser
    return AuthenticatedUser(
        user_id=APPROVER_ID,
        email="approver@test.com",
        workspace_id=WORKSPACE_ID,
        roles=["approver"],
        token_kind="api_token",
    )


class TestCreatePlan:
    async def test_post_plans_creates_plan(self):
        from api.routers.plans import PlanCreateRequest, create_plan

        async with _session_factory() as db:
            body = PlanCreateRequest(
                resource_ids=[VM_ID],
                target_provider="azure",
                target_region="eastus",
                sizing_strategy="like_for_like",
            )
            result = await create_plan(
                body=body,
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_creator_user(),
            )

        assert result.status == "draft"
        assert result.target_provider == "azure"
        assert result.content_hash is not None
        assert len(result.content_hash) == 64

    async def test_post_plans_returns_plan_id(self):
        from api.routers.plans import PlanCreateRequest, create_plan

        async with _session_factory() as db:
            body = PlanCreateRequest(
                resource_ids=[VM_ID],
                target_provider="azure",
                target_region="eastus",
            )
            result = await create_plan(
                body=body,
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_creator_user(),
            )

        assert result.id is not None
        assert isinstance(result.id, uuid.UUID)

    async def test_invalid_provider_raises_422(self):
        from api.routers.plans import PlanCreateRequest, create_plan

        async with _session_factory() as db:
            body = PlanCreateRequest(
                resource_ids=[VM_ID],
                target_provider="invalid_cloud",
                target_region="eastus",
            )
            with pytest.raises(HTTPException) as exc:
                await create_plan(
                    body=body,
                    workspace_id=WORKSPACE_ID,
                    session=db,
                    user=_creator_user(),
                )
        assert exc.value.status_code == 422


class TestGetPlan:
    async def _create_plan(self, db):
        from api.routers.plans import PlanCreateRequest, create_plan
        body = PlanCreateRequest(
            resource_ids=[VM_ID],
            target_provider="azure",
            target_region="eastus",
        )
        return await create_plan(
            body=body,
            workspace_id=WORKSPACE_ID,
            session=db,
            user=_creator_user(),
        )

    async def test_get_plan_returns_full_document(self):
        from api.routers.plans import get_plan

        async with _session_factory() as db:
            summary = await self._create_plan(db)
            result = await get_plan(
                plan_id=summary.id,
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_creator_user(),
            )

        assert result.id == summary.id
        assert result.plan_document is not None
        assert "steps" in result.plan_document
        assert len(result.plan_document["steps"]) >= 10

    async def test_get_plan_404_for_unknown_id(self):
        from api.routers.plans import get_plan

        async with _session_factory() as db:
            with pytest.raises(HTTPException) as exc:
                await get_plan(
                    plan_id=uuid.uuid4(),
                    workspace_id=WORKSPACE_ID,
                    session=db,
                    user=_creator_user(),
                )
        assert exc.value.status_code == 404


class TestExportJson:
    async def test_export_json_contains_schema_version(self):
        import json

        from api.routers.plans import PlanCreateRequest, create_plan, export_json

        async with _session_factory() as db:
            summary = await create_plan(
                body=PlanCreateRequest(
                    resource_ids=[VM_ID],
                    target_provider="azure",
                    target_region="eastus",
                ),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_creator_user(),
            )
            response = await export_json(
                plan_id=summary.id,
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_creator_user(),
            )

        data = json.loads(response.body)
        assert data["schema_version"] == "1.0"
        assert data["name"] is not None


class TestApprovePlan:
    async def _create(self, db):
        from api.routers.plans import PlanCreateRequest, create_plan
        return await create_plan(
            body=PlanCreateRequest(
                resource_ids=[VM_ID],
                target_provider="azure",
                target_region="eastus",
            ),
            workspace_id=WORKSPACE_ID,
            session=db,
            user=_creator_user(),
        )

    async def test_approve_succeeds_with_different_approver(self):
        from api.routers.plans import PlanApproveRequest, approve_plan

        async with _session_factory() as db:
            plan = await self._create(db)
            result = await approve_plan(
                plan_id=plan.id,
                body=PlanApproveRequest(note="LGTM"),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_approver_user(),
            )

        assert result.status == "approved"
        assert result.plan_hash is not None
        assert len(result.plan_hash) == 64

    async def test_self_approval_is_rejected(self):
        """Creator cannot approve their own plan."""
        from api.auth import AuthenticatedUser
        from api.routers.plans import PlanApproveRequest, approve_plan

        # Make a user who has approver role but same ID as creator
        same_user = AuthenticatedUser(
            user_id=CREATOR_ID,
            email="creator@test.com",
            workspace_id=WORKSPACE_ID,
            roles=["approver"],
            token_kind="api_token",
        )

        async with _session_factory() as db:
            plan = await self._create(db)
            with pytest.raises(HTTPException) as exc:
                await approve_plan(
                    plan_id=plan.id,
                    body=PlanApproveRequest(),
                    workspace_id=WORKSPACE_ID,
                    session=db,
                    user=same_user,
                )

        assert exc.value.status_code == 403
        assert "self-approval" in exc.value.detail.lower()

    async def test_approved_plan_cannot_be_approved_again(self):
        from api.routers.plans import PlanApproveRequest, approve_plan

        async with _session_factory() as db:
            plan = await self._create(db)
            # First approval
            await approve_plan(
                plan_id=plan.id,
                body=PlanApproveRequest(),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=_approver_user(),
            )
            # Second approval must fail
            with pytest.raises(HTTPException) as exc:
                await approve_plan(
                    plan_id=plan.id,
                    body=PlanApproveRequest(),
                    workspace_id=WORKSPACE_ID,
                    session=db,
                    user=_approver_user(),
                )

        assert exc.value.status_code == 409
