"""API behaviour end to end (real DB; fake IdP, OpenBao and Temporal)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from aether.core.connections import CheckResult, ConnectionTestResult
from aether.core.enums import CheckStatus
from aether.providers.base.adapter import ConnCtx
from aether.workers.connector import ConnectorActivities

from .conftest import ADMIN, FakeBao, auth, make_member

pytestmark = pytest.mark.integration

ROLE_ARN = "arn:aws:iam::123456789012:role/AetherReadOnly"
AKID = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def conns(ws: dict[str, Any]) -> str:
    return f"/api/v1/workspaces/{ws['id']}/connections"


async def test_health_and_client_config(client: httpx.AsyncClient) -> None:
    assert (await client.get("/livez")).json() == {"status": "ok"}
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] == "ok"
    cfg = (await client.get("/api/v1/meta/client-config")).json()
    assert cfg["oidc_client_id"] == "aether-web"
    assert "x-request-id" in r.headers


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


async def test_jit_provisioning_and_admin_only_workspace_creation(client: httpx.AsyncClient) -> None:
    me = (await client.get("/api/v1/me", headers=auth("plain-user"))).json()
    assert me["is_platform_admin"] is False
    assert me["memberships"] == []
    r = await client.post(
        "/api/v1/workspaces", json={"slug": "nope", "name": "n"}, headers=auth("plain-user")
    )
    assert r.status_code == 403


async def test_workspace_membership_enforced(client: httpx.AsyncClient, workspace: dict[str, Any]) -> None:
    outsider = auth("outsider")
    r = await client.get(conns(workspace), headers=outsider)
    assert r.status_code == 403
    viewer = await make_member(client, workspace["id"], "viewer")
    assert (await client.get(conns(workspace), headers=viewer)).status_code == 200
    r = await client.post(
        conns(workspace),
        json={
            "name": "x",
            "provider": "aws",
            "config": {"auth_method": "aws_assume_role", "role_arn": ROLE_ARN},
        },
        headers=viewer,
    )
    assert r.status_code == 403
    assert "connection-admin" in r.json()["error"]["message"]


async def test_assume_role_connection_lifecycle(client: httpx.AsyncClient, workspace: dict[str, Any]) -> None:
    admin = await make_member(client, workspace["id"], "connection-admin")
    r = await client.post(
        conns(workspace),
        json={
            "name": "aws-prod",
            "provider": "aws",
            "config": {
                "auth_method": "aws_assume_role",
                "role_arn": ROLE_ARN,
                "external_id": "user-chosen-should-be-ignored",
                "regions": ["eu-central-1"],
            },
        },
        headers=admin,
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "untested"
    assert c["config"]["external_id"].startswith("aether-")
    assert c["config"]["external_id"] != "user-chosen-should-be-ignored"
    assert c["has_secret"] is False

    setup = (await client.get(f"{conns(workspace)}/{c['id']}/setup", headers=admin)).json()
    cond = setup["trust_policy"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond["sts:ExternalId"] == c["config"]["external_id"]
    assert "ec2:DescribeInstances" in setup["permissions_policy"]["Statement"][0]["Action"]

    dup = await client.post(
        conns(workspace),
        json={
            "name": "aws-prod",
            "provider": "aws",
            "config": {"auth_method": "aws_assume_role", "role_arn": ROLE_ARN},
        },
        headers=admin,
    )
    assert dup.status_code == 409

    r = await client.patch(f"{conns(workspace)}/{c['id']}", json={"regions": ["us-east-1"]}, headers=admin)
    assert r.json()["config"]["regions"] == ["us-east-1"]
    bad = await client.patch(f"{conns(workspace)}/{c['id']}", json={"regions": ["nowhere"]}, headers=admin)
    assert bad.status_code == 422

    assert (await client.delete(f"{conns(workspace)}/{c['id']}", headers=admin)).status_code == 204
    assert (await client.get(f"{conns(workspace)}/{c['id']}", headers=admin)).status_code == 404

    audit = (await client.get(f"/api/v1/workspaces/{workspace['id']}/audit", headers=admin)).json()["items"]
    actions = [e["action"] for e in audit]
    assert actions[:3] == ["connection.delete", "connection.update", "connection.create"]


async def test_access_key_secret_goes_to_openbao_only(
    client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao
) -> None:
    admin = await make_member(client, workspace["id"], "connection-admin")
    r = await client.post(
        conns(workspace),
        json={
            "name": "aws-keys",
            "provider": "aws",
            "config": {"auth_method": "aws_access_key"},
            "secret": {"access_key_id": AKID, "secret_access_key": SECRET},
        },
        headers=admin,
    )
    assert r.status_code == 201, r.text
    body = r.text
    assert SECRET not in body
    assert AKID not in body
    c = r.json()
    assert c["has_secret"] is True
    assert c["secret_version"] == 1
    assert bao.store[f"ws/{workspace['id']}/conn/{c['id']}"][0]["secret_access_key"] == SECRET

    audit = (await client.get(f"/api/v1/workspaces/{workspace['id']}/audit", headers=admin)).text
    assert SECRET not in audit
    assert AKID not in audit

    # Validation errors must not echo submitted secrets back.
    r = await client.post(
        conns(workspace),
        json={
            "name": "bad",
            "provider": "aws",
            "config": {"auth_method": "aws_access_key"},
            "secret": {"access_key_id": "not-a-key", "secret_access_key": SECRET},
        },
        headers=admin,
    )
    assert r.status_code == 422
    assert SECRET not in r.text

    # Rotation bumps the version and resets the status.
    r = await client.patch(
        f"{conns(workspace)}/{c['id']}",
        json={"secret": {"access_key_id": AKID, "secret_access_key": SECRET + "2"}},
        headers=admin,
    )
    assert r.json()["secret_version"] == 2
    await client.delete(f"{conns(workspace)}/{c['id']}", headers=admin)
    assert f"ws/{workspace['id']}/conn/{c['id']}" not in bao.store


async def test_rejects_unsupported_provider_and_execute_mode(
    client: httpx.AsyncClient, workspace: dict[str, Any]
) -> None:
    body = {
        "name": "x",
        "provider": "aws",
        "config": {"auth_method": "aws_assume_role", "role_arn": ROLE_ARN},
    }
    r = await client.post(conns(workspace), json={**body, "mode": "execute"}, headers=ADMIN)
    assert r.status_code == 422
    r = await client.post(conns(workspace), json={**body, "provider": "azure"}, headers=ADMIN)
    assert r.status_code == 422


class _StubAws:
    def __init__(self) -> None:
        self.seen: ConnCtx | None = None

    def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult:
        self.seen = ConnCtx(ctx.connection_id, ctx.auth_method, ctx.config, dict(ctx.secret or {}))
        from datetime import UTC, datetime

        return ConnectionTestResult(
            status=ConnectionTestResult.overall([]),
            identity={"account": "123456789012", "arn": "arn:aws:iam::123456789012:user/aether"},
            checks=[CheckResult(id="auth", status=CheckStatus.PASS, message="ok")],
            cloud_calls=["sts:GetCallerIdentity:200"],
            tested_at=datetime.now(UTC),
        )


class _InlineTemporal:
    """Runs the connector activity in-process instead of via a Temporal server."""

    def __init__(self, acts: ConnectorActivities) -> None:
        self.acts = acts

    async def execute_workflow(self, _wf: Any, inp: Any, **_: Any) -> dict[str, Any]:
        return await self.acts.test_connection(inp)


async def test_connection_test_runs_in_connector_and_is_audited(
    app: FastAPI, client: httpx.AsyncClient, workspace: dict[str, Any], bao: FakeBao
) -> None:
    stub = _StubAws()
    app.state.temporal = _InlineTemporal(ConnectorActivities(bao, stub))  # type: ignore[arg-type]
    admin = await make_member(client, workspace["id"], "connection-admin")
    c = (
        await client.post(
            conns(workspace),
            json={
                "name": "aws-test",
                "provider": "aws",
                "config": {"auth_method": "aws_access_key"},
                "secret": {"access_key_id": AKID, "secret_access_key": SECRET},
            },
            headers=admin,
        )
    ).json()
    r = await client.post(f"{conns(workspace)}/{c['id']}/test", headers=admin)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ok"
    assert out["last_test_result"]["identity"]["account"] == "123456789012"
    # The connector (not the API) received the secret from OpenBao.
    assert stub.seen is not None
    assert stub.seen.secret == {"access_key_id": AKID, "secret_access_key": SECRET}

    events = (
        await client.get(f"/api/v1/workspaces/{workspace['id']}/audit?action=connection.test", headers=admin)
    ).json()
    ev = events["items"][0]
    assert ev["details"]["cloud_calls"] == ["sts:GetCallerIdentity:200"]
    assert ev["actor_display"]
    assert ev["actor_display"].endswith("@example.com")

    v = (await client.get("/api/v1/audit/verify", headers=ADMIN)).json()
    assert v["ok"] is True
    assert (await client.get("/api/v1/audit/verify", headers=admin)).status_code == 403
