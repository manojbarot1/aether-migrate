"""AWS adapter tests against moto (no network)."""

import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from aether.core.connections import CheckResult
from aether.core.enums import AuthMethod, CheckStatus, ConnectionStatus
from aether.providers.aws.adapter import AwsAdapter
from aether.providers.base.adapter import ConnCtx

MOTO_ACCOUNT = "123456789012"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        yield


def _iam_user_with_policy(actions: list[str]) -> dict[str, str]:
    iam = boto3.client("iam", region_name="us-east-1")
    iam.create_user(UserName="aether")
    iam.put_user_policy(
        UserName="aether",
        PolicyName="p",
        PolicyDocument=json.dumps(
            {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": actions, "Resource": "*"}]}
        ),
    )
    key = iam.create_access_key(UserName="aether")["AccessKey"]
    return {"access_key_id": key["AccessKeyId"], "secret_access_key": key["SecretAccessKey"]}


def test_access_key_connection_ok(aws: None) -> None:
    secret = _iam_user_with_policy(["ec2:Describe*", "sts:GetCallerIdentity"])
    ctx = ConnCtx("c0ffee00-0000", AuthMethod.AWS_ACCESS_KEY, {"regions": ["eu-central-1"]}, secret=secret)
    result = AwsAdapter().test_connection(ctx)

    by_id = {c.id: c for c in result.checks}
    assert by_id["auth"].status == CheckStatus.PASS
    assert by_id["regions"].status == CheckStatus.PASS
    assert result.identity["account"] == MOTO_ACCOUNT
    assert any(c.startswith("sts:GetCallerIdentity:") for c in result.cloud_calls)
    assert any(c.startswith("ec2:DescribeRegions:") for c in result.cloud_calls)
    # No secret material anywhere in the result.
    dumped = result.model_dump_json()
    assert secret["secret_access_key"] not in dumped
    assert secret["access_key_id"] not in dumped


def test_unknown_region_warns(aws: None) -> None:
    secret = _iam_user_with_policy(["ec2:Describe*"])
    ctx = ConnCtx("c0ffee00", AuthMethod.AWS_ACCESS_KEY, {"regions": ["xx-nowhere-9"]}, secret=secret)
    result = AwsAdapter().test_connection(ctx)
    regions = next(c for c in result.checks if c.id == "regions")
    assert regions.status == CheckStatus.WARN
    assert result.status in (ConnectionStatus.WARNING, ConnectionStatus.ERROR)


def test_assume_role_with_external_id(aws: None) -> None:
    iam = boto3.client("iam", region_name="us-east-1")
    role_arn = iam.create_role(
        RoleName="AetherReadOnly",
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}],
            }
        ),
    )["Role"]["Arn"]
    ctx = ConnCtx(
        "c0ffee00",
        AuthMethod.AWS_ASSUME_ROLE,
        {"role_arn": role_arn, "external_id": "aether-abc", "regions": []},
        platform_secret={"access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "x" * 40},
    )
    result = AwsAdapter().test_connection(ctx)
    by_id = {c.id: c for c in result.checks}
    assert by_id["auth"].status == CheckStatus.PASS, by_id["auth"].message
    assert by_id["account_match"].status == CheckStatus.PASS
    assert "assumed-role/AetherReadOnly" in result.identity["arn"]
    assert any(c.startswith("sts:AssumeRole:") for c in result.cloud_calls)


class _FakeClient:
    def __init__(self, service: str, account: str) -> None:
        self.service, self.account = service, account

    def assume_role(self, **_: object) -> dict[str, object]:
        return {
            "Credentials": {"AccessKeyId": "ASIA" + "A" * 16, "SecretAccessKey": "s", "SessionToken": "t"}
        }

    def get_caller_identity(self) -> dict[str, str]:
        return {
            "Account": self.account,
            "Arn": f"arn:aws:sts::{self.account}:assumed-role/X/s",
            "UserId": "u",
        }


class _FakeSession:
    def __init__(self, account: str) -> None:
        self.account = account
        self.events = type("E", (), {"register": staticmethod(lambda *a, **k: None)})()

    def client(self, service: str, **_: object) -> _FakeClient:
        return _FakeClient(service, self.account)


def test_account_mismatch_fails() -> None:
    adapter = AwsAdapter(session_factory=lambda **_: _FakeSession("111111111111"))  # type: ignore[arg-type,return-value]
    ctx = ConnCtx(
        "c0ffee00",
        AuthMethod.AWS_ASSUME_ROLE,
        {"role_arn": "arn:aws:iam::999999999999:role/X", "external_id": "e", "regions": []},
    )
    result = adapter.test_connection(ctx)
    match = next(c for c in result.checks if c.id == "account_match")
    assert match.status == CheckStatus.FAIL
    assert result.status == ConnectionStatus.ERROR


def test_missing_secret_fails_cleanly(aws: None) -> None:
    ctx = ConnCtx("c0ffee00", AuthMethod.AWS_ACCESS_KEY, {"regions": []}, secret=None)
    result = AwsAdapter().test_connection(ctx)
    assert result.status == ConnectionStatus.ERROR
    assert result.checks[0].id == "auth"


def test_unparseable_simulation_response_is_a_warning_not_a_crash(
    aws: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.parsers import ResponseParserError

    secret = _iam_user_with_policy(["ec2:Describe*"])
    adapter = AwsAdapter()

    def boom(*_: object, **__: object) -> list[CheckResult]:
        raise ResponseParserError("Unable to parse response (not well-formed)")

    real = adapter._simulate
    monkeypatch.setattr(adapter, "_simulate", lambda iam, arn: _wrap(real, iam, arn, boom))
    ctx = ConnCtx("c0ffee00", AuthMethod.AWS_ACCESS_KEY, {"regions": []}, secret=secret)
    result = adapter.test_connection(ctx)
    perms = next(c for c in result.checks if c.id == "permissions")
    assert perms.status == CheckStatus.WARN
    assert "ResponseParserError" in perms.message


def _wrap(real, iam, arn, boom):  # type: ignore[no-untyped-def]
    class Iam:
        def get_paginator(self, _op: str) -> object:
            boom()
            raise AssertionError

    return real(Iam(), arn)
