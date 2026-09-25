"""Integration tests for the assessment API router.

Uses an in-memory SQLite database. Seeds a VM with known issues and tests:
- POST /assessment/run
- POST /assessment/findings/.../acknowledge
- GET /assessment/results/{resource_id}
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
from db.models import (
    Base,
    CatalogInstanceTypeRow,
    ConnectionRow,
    ResourceRow,
    UserRow,
    WorkspaceRow,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

WORKSPACE_ID = uuid.UUID("eeee0000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("ffff0000-0000-0000-0000-000000000001")
CONNECTION_ID = uuid.UUID("9999aaaa-0000-0000-0000-000000000001")

_seeded_resource_id: uuid.UUID | None = None


@pytest.fixture(scope="module", autouse=True)
async def setup_db():
    global _seeded_resource_id

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _session_factory() as db:
        # Workspace
        db.add(WorkspaceRow(
            id=WORKSPACE_ID,
            name="Test Workspace",
            slug="test-workspace",
        ))
        # User
        db.add(UserRow(
            id=USER_ID,
            workspace_id=WORKSPACE_ID,
            email="analyst@test.com",
            name="Test Analyst",
            role="analyst",
        ))
        # Connection
        db.add(ConnectionRow(
            id=CONNECTION_ID,
            workspace_id=WORKSPACE_ID,
            provider="aws",
            name="Test AWS",
            mode="read-only",
        ))

        # VM with known issues: arm64, Windows 2012, ephemeral disk
        resource_id = uuid.uuid4()
        _seeded_resource_id = resource_id
        db.add(ResourceRow(
            id=resource_id,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            provider="aws",
            native_id="i-known-issues-001",
            account="123456789",
            region="us-east-1",
            kind="vm",
            name="known-issues-vm",
            status="running",
            tags={},
            spec={
                "os_name": "Windows Server",
                "os_version": "2012 R2",
                "architecture": "arm64",
                "vcpu": 8,
                "memory_gib": 32.0,
                "instance_type": "m6g.xlarge",
                "disks": [
                    {"size_gib": 100, "boot": True, "ephemeral": False},
                    {"size_gib": 50, "ephemeral": True},
                ],
                "nics": [
                    {"private_ips": ["10.0.1.10"], "public_ips": [], "security_group_ids": []}
                ],
                "extra": {},
            },
            provenance={},
        ))

        # Catalog: no arm64 in eastus
        db.add(CatalogInstanceTypeRow(
            id=uuid.uuid4(),
            provider="azure",
            catalog_version="2025-01",
            effective_from=datetime(2025, 1, 1),
            region="eastus",
            sku="Standard_D8s_v5",
            vcpu=8,
            memory_mib=32768,
            cpu_arch="x86_64",  # No arm64!
            local_nvme_gib=0,
            os_support={"linux": True, "windows": True},
            available_in_region=True,
            restricted=False,
        ))

        await db.commit()

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _make_mock_user(role: str = "analyst") -> Any:
    from api.auth import AuthenticatedUser
    return AuthenticatedUser(
        user_id=USER_ID,
        email="analyst@test.com",
        workspace_id=WORKSPACE_ID,
        roles=[role],
        token_kind="api_token",
    )


async def _get_db():
    async with _session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Test: POST /assessment/run
# ---------------------------------------------------------------------------


class TestAssessmentRunEndpoint:
    async def test_run_returns_blockers_for_arm64_and_eol_windows(self) -> None:
        from api.dependencies import get_db_session, get_workspace_id
        from api.routers.assessment import AssessmentRunRequest, router, run_assessment
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/assessment")

        # Patch auth dependencies
        user = _make_mock_user()
        workspace_override = lambda: WORKSPACE_ID

        app.dependency_overrides[get_workspace_id] = workspace_override
        app.dependency_overrides[get_db_session] = _get_db

        from api.dependencies import require_role
        app.dependency_overrides[require_role("analyst")] = lambda: user

        # Direct call to the endpoint function
        async with _session_factory() as db:
            request = AssessmentRunRequest(
                resource_id=_seeded_resource_id,
                target_provider="azure",
                target_region="eastus",
            )
            result = await run_assessment(
                body=request,
                workspace_id=WORKSPACE_ID,
                session=db,
                user=user,
            )

        # Should detect: CPU-001 (arm64 not in eastus), OS-002 (Windows 2012 EOL),
        # DISK-001 (ephemeral disk), NET-001 (private IP), DRV-001 (AWS→Azure)
        rule_ids = {f.rule_id for f in result.findings}
        assert "CPU-001" in rule_ids, f"Expected CPU-001 in {rule_ids}"
        assert result.readiness.blockers > 0  # arm64 with no arm64 SKU = blocker

    async def test_run_returns_404_for_unknown_resource(self) -> None:
        from api.routers.assessment import AssessmentRunRequest, run_assessment
        from fastapi import HTTPException

        user = _make_mock_user()
        async with _session_factory() as db:
            with pytest.raises(HTTPException) as exc:
                await run_assessment(
                    body=AssessmentRunRequest(
                        resource_id=uuid.uuid4(),
                        target_provider="azure",
                        target_region="eastus",
                    ),
                    workspace_id=WORKSPACE_ID,
                    session=db,
                    user=user,
                )
        assert exc.value.status_code == 404

    async def test_run_returns_422_for_unknown_provider(self) -> None:
        from api.routers.assessment import AssessmentRunRequest, run_assessment
        from fastapi import HTTPException

        user = _make_mock_user()
        async with _session_factory() as db:
            with pytest.raises(HTTPException) as exc:
                await run_assessment(
                    body=AssessmentRunRequest(
                        resource_id=_seeded_resource_id,
                        target_provider="oracle",  # not a valid provider
                        target_region="us-phoenix-1",
                    ),
                    workspace_id=WORKSPACE_ID,
                    session=db,
                    user=user,
                )
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Test: acknowledge endpoints
# ---------------------------------------------------------------------------


class TestAcknowledgeEndpoint:
    async def test_acknowledge_creates_record(self) -> None:
        from api.routers.assessment import AcknowledgeRequest, acknowledge_finding
        from db.models import FindingAcknowledgementRow
        from sqlalchemy import select

        user = _make_mock_user()
        async with _session_factory() as db:
            resp = await acknowledge_finding(
                resource_id=_seeded_resource_id,
                rule_id="CPU-001",
                body=AcknowledgeRequest(reason="Accepted for now", expires_at=None),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=user,
            )

        assert resp.rule_id == "CPU-001"
        assert resp.reason == "Accepted for now"

        # Verify persisted
        async with _session_factory() as db:
            result = await db.execute(
                select(FindingAcknowledgementRow).where(
                    FindingAcknowledgementRow.workspace_id == WORKSPACE_ID,
                    FindingAcknowledgementRow.resource_id == _seeded_resource_id,
                    FindingAcknowledgementRow.rule_id == "CPU-001",
                )
            )
            ack_row = result.scalar_one_or_none()

        assert ack_row is not None
        assert ack_row.reason == "Accepted for now"

    async def test_acknowledge_reflects_in_subsequent_run(self) -> None:
        """After acknowledging CPU-001, running the assessment should show it as acknowledged."""
        from api.routers.assessment import AssessmentRunRequest, run_assessment

        user = _make_mock_user()
        async with _session_factory() as db:
            result = await run_assessment(
                body=AssessmentRunRequest(
                    resource_id=_seeded_resource_id,
                    target_provider="azure",
                    target_region="eastus",
                ),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=user,
            )

        cpu_findings = [f for f in result.findings if f.rule_id == "CPU-001"]
        assert cpu_findings, "CPU-001 should still be in findings"
        assert cpu_findings[0].acknowledged is True


# ---------------------------------------------------------------------------
# Test: GET /assessment/results
# ---------------------------------------------------------------------------


class TestGetResultsEndpoint:
    async def test_get_results_returns_history(self) -> None:
        """After running an assessment, results should appear in history."""
        from api.routers.assessment import (
            AssessmentRunRequest,
            list_assessment_results,
            run_assessment,
        )

        user = _make_mock_user()

        # Ensure at least one run is persisted
        async with _session_factory() as db:
            await run_assessment(
                body=AssessmentRunRequest(
                    resource_id=_seeded_resource_id,
                    target_provider="azure",
                    target_region="eastus",
                ),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=user,
            )

        async with _session_factory() as db:
            history = await list_assessment_results(
                resource_id=_seeded_resource_id,
                workspace_id=WORKSPACE_ID,
                session=db,
                user=user,
            )

        assert len(history) > 0
        assert history[0].target_provider == "azure"
        assert history[0].target_region == "eastus"
        assert history[0].readiness_score >= 0

    async def test_get_results_empty_for_unknown_resource(self) -> None:
        from api.routers.assessment import list_assessment_results

        user = _make_mock_user()
        async with _session_factory() as db:
            history = await list_assessment_results(
                resource_id=uuid.uuid4(),
                workspace_id=WORKSPACE_ID,
                session=db,
                user=user,
            )

        assert history == []
