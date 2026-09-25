"""AWS credential model and connection tester.

``AWSConnectionTester`` verifies that credentials stored in OpenBao are valid
and checks for excess IAM permissions that violate the discovery-only contract.

Excess-permission checks (read-only violations):
- s3:GetObject  — cloud-object access should not be granted
- ec2:TerminateInstances — destructive action, must NOT be granted

boto3 is imported here (``packages/providers/aws``) and MUST NOT be imported
anywhere in the ``apps/api`` package.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


class AWSCredentials(BaseModel):
    """AWS credential bundle fetched from OpenBao — never persisted in Postgres."""

    role_arn: str | None = None
    external_id: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    default_region: str = "us-east-1"


# Actions that discovery must NOT be allowed to perform
_EXCESS_ACTIONS = [
    "s3:GetObject",
    "ec2:TerminateInstances",
]


class AWSConnectionTester:
    """Validates AWS credentials and checks for excess IAM permissions.

    All boto3 calls are run via ``asyncio.get_event_loop().run_in_executor``
    so that the async event loop is not blocked by blocking SDK calls.
    """

    async def test(self, creds: AWSCredentials) -> ConnectionTestResult:  # noqa: F821
        """Test *creds* and return a ``ConnectionTestResult``.

        Steps:
        1. Build a boto3 session from the supplied credentials.
        2. If ``role_arn`` is set, assume the role via STS (with ExternalId).
        3. Call ``sts.get_caller_identity()`` to verify the identity.
        4. Call ``iam.simulate_principal_policy()`` to detect excess permissions.
        5. Return ``ConnectionTestResult`` with identity ARN and any warnings.
        """
        # Lazy import — boto3 only lives in packages/providers/aws
        import boto3  # type: ignore[import-untyped]
        from api.schemas.connections import ConnectionTestResult
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

        loop = asyncio.get_event_loop()

        def _run() -> ConnectionTestResult:
            warnings: list[str] = []
            errors: list[str] = []
            identity: str | None = None

            try:
                # Step 1 — base session
                session_kwargs: dict[str, Any] = {
                    "region_name": creds.default_region,
                }
                if creds.access_key_id and creds.secret_access_key:
                    session_kwargs["aws_access_key_id"] = creds.access_key_id
                    session_kwargs["aws_secret_access_key"] = creds.secret_access_key

                base_session = boto3.Session(**session_kwargs)

                # Step 2 — assume role if role_arn is set
                if creds.role_arn:
                    sts_client = base_session.client("sts")
                    assume_kwargs: dict[str, Any] = {
                        "RoleArn": creds.role_arn,
                        "RoleSessionName": "aether-migrate-connection-test",
                    }
                    if creds.external_id:
                        assume_kwargs["ExternalId"] = creds.external_id
                    assume_resp = sts_client.assume_role(**assume_kwargs)
                    assumed_creds = assume_resp["Credentials"]
                    # Build a new session with the temporary credentials
                    active_session = boto3.Session(
                        aws_access_key_id=assumed_creds["AccessKeyId"],
                        aws_secret_access_key=assumed_creds["SecretAccessKey"],
                        aws_session_token=assumed_creds["SessionToken"],
                        region_name=creds.default_region,
                    )
                else:
                    active_session = base_session

                # Step 3 — verify identity
                sts = active_session.client("sts")
                caller = sts.get_caller_identity()
                identity = caller["Arn"]
                log.info("connection.test.identity_verified", identity=identity)

                # Step 4 — check for excess permissions via IAM policy simulation
                iam = active_session.client("iam")
                sim_resp = iam.simulate_principal_policy(
                    PolicySourceArn=identity,
                    ActionNames=_EXCESS_ACTIONS,
                    ResourceArns=["*"],
                )
                for result in sim_resp.get("EvaluationResults", []):
                    action = result.get("EvalActionName", "")
                    decision = result.get("EvalDecision", "")
                    if decision == "allowed":
                        msg = (
                            f"Excess permission detected: {action} is allowed. "
                            "The discovery policy should not grant this action."
                        )
                        warnings.append(msg)
                        log.warning(
                            "connection.test.excess_permission",
                            action=action,
                            identity=identity,
                        )

            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                msg = f"AWS API error ({error_code}): {exc.response['Error'].get('Message', '')}"
                errors.append(msg)
                log.error("connection.test.client_error", error_code=error_code)
                return ConnectionTestResult(ok=False, identity=identity, warnings=warnings, errors=errors)
            except BotoCoreError as exc:
                errors.append(f"Boto3 error: {type(exc).__name__}")
                log.error("connection.test.botocore_error", exc_type=type(exc).__name__)
                return ConnectionTestResult(ok=False, identity=identity, warnings=warnings, errors=errors)

            return ConnectionTestResult(
                ok=len(errors) == 0,
                identity=identity,
                warnings=warnings,
                errors=errors,
            )

        return await loop.run_in_executor(None, _run)
