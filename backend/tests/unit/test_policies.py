import ast
from pathlib import Path

from aether.core.policies import (
    AWS_DISCOVERY_ACTIONS,
    AWS_EXCESS_PROBE_ACTIONS,
    aws_permissions_policy,
    aws_trust_policy,
)

ADAPTER = Path(__file__).parents[2] / "src/aether/providers/aws/adapter.py"

# boto3 method name -> IAM action. Extend when the adapter calls new operations.
BOTO_TO_IAM = {
    "get_caller_identity": "sts:GetCallerIdentity",
    "describe_regions": "ec2:DescribeRegions",
    "assume_role": None,  # governed by the customer's trust policy, not the permissions policy
    "get_paginator": None,
}


def _called_methods() -> set[str]:
    tree = ast.parse(ADAPTER.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names & (set(BOTO_TO_IAM) | {m for m in names if m.startswith(("describe_", "get_", "list_"))})


def test_every_adapter_call_is_in_the_policy() -> None:
    for method in _called_methods():
        assert method in BOTO_TO_IAM, f"map {method} to its IAM action in BOTO_TO_IAM"
        action = BOTO_TO_IAM[method]
        if action:
            assert action in AWS_DISCOVERY_ACTIONS, f"{action} missing from AWS_DISCOVERY_ACTIONS"


def test_policy_is_read_only() -> None:
    for action in AWS_DISCOVERY_ACTIONS:
        verb = action.split(":")[1]
        assert verb.startswith(("Describe", "Get", "List")), action
    assert not set(AWS_DISCOVERY_ACTIONS) & set(AWS_EXCESS_PROBE_ACTIONS)
    assert aws_permissions_policy()["Statement"][0]["Resource"] == "*"


def test_trust_policy_requires_external_id() -> None:
    tp = aws_trust_policy("arn:aws:iam::111111111111:role/aether", "aether-xyz")
    assert tp["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == "aether-xyz"
