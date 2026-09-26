import pytest
from pydantic import TypeAdapter, ValidationError

from aether.api.schemas import ConnectionCreate
from aether.core.connections import AwsAccessKeySecret, AwsAssumeRoleConfig, ConnectionConfig


def test_role_arn_validation() -> None:
    cfg = AwsAssumeRoleConfig(
        role_arn="arn:aws:iam::123456789012:role/Aether", regions=["eu-central-1", "eu-central-1"]
    )
    assert cfg.account_id == "123456789012"
    assert cfg.regions == ["eu-central-1"]
    with pytest.raises(ValidationError):
        AwsAssumeRoleConfig(role_arn="arn:aws:iam::123:user/x")
    with pytest.raises(ValidationError):
        AwsAssumeRoleConfig(role_arn="arn:aws:iam::123456789012:role/A", regions=["mars-1"])


def test_discriminated_config() -> None:
    ta = TypeAdapter(ConnectionConfig)
    assert ta.validate_python({"auth_method": "aws_access_key"}).auth_method == "aws_access_key"
    with pytest.raises(ValidationError):
        ta.validate_python({"auth_method": "nope"})


def test_secret_never_rendered() -> None:
    s = AwsAccessKeySecret(access_key_id="AKIAIOSFODNN7EXAMPLE", secret_access_key="wJalrXUtnFEMI/K7MDENG")
    assert "wJalr" not in repr(s)
    assert "wJalr" not in s.model_dump_json()
    with pytest.raises(ValidationError):
        AwsAccessKeySecret(access_key_id="ASIAIOSFODNN7EXAMPLE", secret_access_key="x")  # temporary key


def test_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ConnectionCreate.model_validate(
            {"name": "x", "provider": "aws", "config": {"auth_method": "aws_access_key"}, "extra": 1}
        )
