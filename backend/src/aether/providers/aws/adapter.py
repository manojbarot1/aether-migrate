"""AWS adapter: authentication and connection testing (Phase 1)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from aether.core.connections import CheckResult, ConnectionTestResult
from aether.core.enums import AuthMethod, CheckStatus, Provider
from aether.core.policies import AWS_DISCOVERY_ACTIONS, AWS_EXCESS_PROBE_ACTIONS
from aether.providers.base.adapter import AdapterCapabilities, ConnCtx

DEFAULT_REGION = "us-east-1"
BOTO_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 5},
    connect_timeout=5,
    read_timeout=20,
    user_agent_appid="aether-migrate",  # type: ignore[call-arg]  # stubs lag botocore
)


class CallRecorder:
    """Records every AWS API call made through a session (service:operation:outcome).

    Attached via botocore's event system so no call can bypass it.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def after_call(self, http_response: Any, parsed: dict[str, Any], model: Any, **_: Any) -> None:
        code = parsed.get("Error", {}).get("Code") if isinstance(parsed, dict) else None
        outcome = code or str(getattr(http_response, "status_code", "ok"))
        with self._lock:
            self.calls.append(f"{model.service_model.service_name}:{model.name}:{outcome}")

    def after_call_error(self, event_name: str, exception: Exception | None = None, **_: Any) -> None:
        # event_name is "after-call-error.<service-id>.<Operation>"
        _event, service, op = [*event_name.split(".", 2), "?", "?"][:3]
        with self._lock:
            self.calls.append(f"{service}:{op}:{type(exception).__name__}")

    def attach(self, session: boto3.session.Session) -> boto3.session.Session:
        events = session.events
        events.register("after-call", self.after_call)
        events.register("after-call-error", self.after_call_error)
        return session


def _err(e: Exception) -> str:
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        return f"{err.get('Code', 'ClientError')}: {err.get('Message', '')}"[:500]
    return type(e).__name__


def _is_access_denied(e: Exception) -> bool:
    return isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") in (
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
    )


SessionFactory = Callable[..., boto3.session.Session]


class AwsAdapter:
    provider = Provider.AWS
    capabilities = AdapterCapabilities(
        metrics=True, actual_cost=True, boot_mode=True, native_dry_run=True, permission_simulation=True
    )

    def __init__(self, session_factory: SessionFactory = boto3.session.Session) -> None:
        self._session_factory = session_factory

    # --- authentication -------------------------------------------------------------

    def _base_session(self, ctx: ConnCtx, region: str) -> boto3.session.Session:
        if ctx.auth_method == AuthMethod.AWS_ACCESS_KEY:
            if not ctx.secret:
                raise ValueError("access-key connection has no stored secret")
            return self._session_factory(
                aws_access_key_id=ctx.secret["access_key_id"],
                aws_secret_access_key=ctx.secret["secret_access_key"],
                region_name=region,
            )
        # Assume-role: start from the platform identity (stored in OpenBao) or, if
        # none is stored, the worker's ambient credentials (e.g. an instance profile).
        if ctx.platform_secret:
            return self._session_factory(
                aws_access_key_id=ctx.platform_secret["access_key_id"],
                aws_secret_access_key=ctx.platform_secret["secret_access_key"],
                region_name=region,
            )
        return self._session_factory(region_name=region)

    def session(
        self, ctx: ConnCtx, recorder: CallRecorder, region: str | None = None
    ) -> boto3.session.Session:
        # Control-plane calls go to the home region; `regions` is only the discovery scope.
        region = region or ctx.config.get("home_region") or DEFAULT_REGION
        base = recorder.attach(self._base_session(ctx, region))
        if ctx.auth_method != AuthMethod.AWS_ASSUME_ROLE:
            return base
        creds = base.client("sts", config=BOTO_CONFIG).assume_role(
            RoleArn=ctx.config["role_arn"],
            RoleSessionName=f"aether-{ctx.connection_id[:8]}",
            ExternalId=ctx.config["external_id"],
            DurationSeconds=900,
        )["Credentials"]
        assumed = self._session_factory(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=region,
        )
        return recorder.attach(assumed)

    # --- connection test ------------------------------------------------------------

    def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult:
        recorder = CallRecorder()
        checks: list[CheckResult] = []
        identity: dict[str, str] = {}

        def done() -> ConnectionTestResult:
            return ConnectionTestResult(
                status=ConnectionTestResult.overall(checks),
                identity=identity,
                checks=checks,
                cloud_calls=recorder.calls,
                tested_at=datetime.now(UTC),
            )

        # 1. Authenticate (assume role if needed) and resolve the caller identity.
        try:
            session = self.session(ctx, recorder)
            ident = session.client("sts", config=BOTO_CONFIG).get_caller_identity()
        except (ClientError, BotoCoreError, ValueError) as e:
            checks.append(
                CheckResult(id="auth", status=CheckStatus.FAIL, message=f"Authentication failed: {_err(e)}")
            )
            return done()
        identity = {"account": ident["Account"], "arn": ident["Arn"], "user_id": ident["UserId"]}
        checks.append(
            CheckResult(id="auth", status=CheckStatus.PASS, message=f"Authenticated as {ident['Arn']}")
        )

        # 2. Account sanity check for assume-role.
        if ctx.auth_method == AuthMethod.AWS_ASSUME_ROLE:
            expected = ctx.config["role_arn"].split(":")[4]
            if ident["Account"] != expected:
                checks.append(
                    CheckResult(
                        id="account_match",
                        status=CheckStatus.FAIL,
                        message=f"Assumed identity is in account {ident['Account']}, expected {expected}",
                    )
                )
                return done()
            checks.append(
                CheckResult(id="account_match", status=CheckStatus.PASS, message="Account matches role ARN")
            )

        # 3. Basic read access and region coverage.
        try:
            regions = session.client("ec2", config=BOTO_CONFIG).describe_regions()["Regions"]
            enabled = sorted(r["RegionName"] for r in regions)
            configured = list(ctx.config.get("regions") or [])
            missing = sorted(set(configured) - set(enabled))
            if missing:
                checks.append(
                    CheckResult(
                        id="regions",
                        status=CheckStatus.WARN,
                        message=f"Configured regions not enabled in this account: {', '.join(missing)}",
                        details={"enabled": enabled, "missing": missing},
                    )
                )
            else:
                scope = ", ".join(configured) if configured else f"all {len(enabled)} enabled regions"
                checks.append(
                    CheckResult(
                        id="regions",
                        status=CheckStatus.PASS,
                        message=f"EC2 read access OK; discovery scope: {scope}",
                        details={"enabled": enabled},
                    )
                )
        except (ClientError, BotoCoreError) as e:
            checks.append(
                CheckResult(id="regions", status=CheckStatus.FAIL, message=f"EC2 read failed: {_err(e)}")
            )

        # 4. Permission simulation: required permissions present, dangerous ones absent.
        principal_arn = (
            ctx.config["role_arn"] if ctx.auth_method == AuthMethod.AWS_ASSUME_ROLE else identity["arn"]
        )
        iam = session.client("iam", config=BOTO_CONFIG)
        checks.extend(self._simulate(iam, principal_arn))
        return done()

    def _simulate(self, iam: Any, principal_arn: str) -> list[CheckResult]:
        def decisions(actions: tuple[str, ...]) -> dict[str, str]:
            out: dict[str, str] = {}
            paginator = iam.get_paginator("simulate_principal_policy")
            for page in paginator.paginate(PolicySourceArn=principal_arn, ActionNames=list(actions)):
                for r in page["EvaluationResults"]:
                    out[r["EvalActionName"]] = r["EvalDecision"]
            return out

        try:
            excess = decisions(AWS_EXCESS_PROBE_ACTIONS)
            required = decisions(AWS_DISCOVERY_ACTIONS)
        except ClientError as e:
            if _is_access_denied(e):
                msg = (
                    "Could not verify permissions: iam:SimulatePrincipalPolicy is not allowed. "
                    "Add the self-simulation statement from the setup policy."
                )
                return [CheckResult(id="permissions", status=CheckStatus.WARN, message=msg)]
            return [
                CheckResult(
                    id="permissions", status=CheckStatus.WARN, message=f"Simulation failed: {_err(e)}"
                )
            ]
        except (BotoCoreError, NotImplementedError) as e:
            return [
                CheckResult(
                    id="permissions", status=CheckStatus.WARN, message=f"Simulation failed: {_err(e)}"
                )
            ]

        results: list[CheckResult] = []
        allowed_excess = sorted(a for a, d in excess.items() if d == "allowed")
        if allowed_excess:
            results.append(
                CheckResult(
                    id="excess_permissions",
                    status=CheckStatus.WARN,
                    message="Credential has more permissions than read-only discovery needs: "
                    + ", ".join(allowed_excess),
                    details={"allowed": allowed_excess},
                )
            )
        else:
            results.append(
                CheckResult(
                    id="excess_permissions", status=CheckStatus.PASS, message="No excess permissions detected"
                )
            )
        missing = sorted(a for a, d in required.items() if d != "allowed")
        if missing:
            results.append(
                CheckResult(
                    id="required_permissions",
                    status=CheckStatus.WARN,
                    message=f"{len(missing)} discovery permission(s) missing; "
                    "discovery coverage will be partial",
                    details={"missing": missing},
                )
            )
        else:
            results.append(
                CheckResult(
                    id="required_permissions",
                    status=CheckStatus.PASS,
                    message="All discovery permissions present",
                )
            )
        return results
