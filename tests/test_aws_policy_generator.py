"""Unit tests for the AWS IAM policy generator.

Tests verify:
- Discovery policy contains required ec2:Describe* actions
- Discovery policy does NOT contain s3:* or iam:* actions
- Trust policy contains ExternalId condition
- Trust policy contains the platform account ARN
"""

from __future__ import annotations

import json

from aws.policy_generator import generate_assume_role_trust_policy, generate_discovery_policy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_actions(policy: dict) -> list[str]:
    """Flatten all Action lists from a policy document into one list."""
    actions: list[str] = []
    for stmt in policy.get("Statement", []):
        raw = stmt.get("Action", [])
        if isinstance(raw, str):
            actions.append(raw)
        else:
            actions.extend(raw)
    return actions


# ---------------------------------------------------------------------------
# Discovery policy tests
# ---------------------------------------------------------------------------


class TestGenerateDiscoveryPolicy:
    def test_returns_valid_policy_structure(self) -> None:
        policy = generate_discovery_policy()
        assert policy["Version"] == "2012-10-17"
        assert isinstance(policy["Statement"], list)
        assert len(policy["Statement"]) > 0

    def test_contains_required_ec2_describe_actions(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        required = [
            "ec2:DescribeInstances",
            "ec2:DescribeVolumes",
            "ec2:DescribeVpcs",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeRegions",
        ]
        for action in required:
            assert action in actions, f"Missing required action: {action}"

    def test_contains_elb_describe_actions(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        assert "elasticloadbalancing:DescribeLoadBalancers" in actions

    def test_contains_cloudwatch_get_metric_data(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        assert "cloudwatch:GetMetricData" in actions

    def test_contains_sts_get_caller_identity(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        assert "sts:GetCallerIdentity" in actions

    def test_does_not_contain_s3_actions(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        s3_actions = [a for a in actions if a.startswith("s3:")]
        assert s3_actions == [], f"Policy must not grant s3:* but found: {s3_actions}"

    def test_does_not_contain_iam_actions(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        iam_actions = [a for a in actions if a.startswith("iam:")]
        assert iam_actions == [], f"Policy must not grant iam:* but found: {iam_actions}"

    def test_does_not_contain_terminate_instances(self) -> None:
        policy = generate_discovery_policy()
        actions = _all_actions(policy)
        assert "ec2:TerminateInstances" not in actions

    def test_does_not_contain_wildcard_s3(self) -> None:
        """Verify the raw JSON does not contain an s3:* wildcard anywhere."""
        policy = generate_discovery_policy()
        policy_json = json.dumps(policy)
        assert "s3:*" not in policy_json

    def test_all_statements_are_allow(self) -> None:
        """Discovery policy should only Allow — no explicit Deny statements."""
        policy = generate_discovery_policy()
        for stmt in policy["Statement"]:
            assert stmt["Effect"] == "Allow", (
                f"Statement '{stmt.get('Sid', '')}' has Effect={stmt['Effect']}; expected Allow"
            )

    def test_is_json_serializable(self) -> None:
        policy = generate_discovery_policy()
        serialized = json.dumps(policy)
        assert len(serialized) > 100


# ---------------------------------------------------------------------------
# Trust policy tests
# ---------------------------------------------------------------------------


class TestGenerateAssumeRoleTrustPolicy:
    def test_returns_valid_policy_structure(self) -> None:
        policy = generate_assume_role_trust_policy(
            external_id="aether-migrate-test-123",
            platform_account_id="123456789012",
        )
        assert policy["Version"] == "2012-10-17"
        assert len(policy["Statement"]) == 1

    def test_contains_external_id_condition(self) -> None:
        ext_id = "aether-migrate-test-abc-def"
        policy = generate_assume_role_trust_policy(
            external_id=ext_id,
            platform_account_id="123456789012",
        )
        stmt = policy["Statement"][0]
        condition = stmt.get("Condition", {})
        assert condition["StringEquals"]["sts:ExternalId"] == ext_id

    def test_contains_platform_account_arn(self) -> None:
        platform_account = "111122223333"
        policy = generate_assume_role_trust_policy(
            external_id="aether-migrate-workspace",
            platform_account_id=platform_account,
        )
        stmt = policy["Statement"][0]
        principal = stmt["Principal"]["AWS"]
        assert platform_account in principal
        assert principal == f"arn:aws:iam::{platform_account}:root"

    def test_action_is_assume_role(self) -> None:
        policy = generate_assume_role_trust_policy(
            external_id="some-external-id",
            platform_account_id="000000000000",
        )
        stmt = policy["Statement"][0]
        assert stmt["Action"] == "sts:AssumeRole"

    def test_effect_is_allow(self) -> None:
        policy = generate_assume_role_trust_policy(
            external_id="some-external-id",
            platform_account_id="000000000000",
        )
        stmt = policy["Statement"][0]
        assert stmt["Effect"] == "Allow"

    def test_different_workspaces_produce_different_external_ids(self) -> None:
        """Each workspace should get a unique external_id."""
        p1 = generate_assume_role_trust_policy(
            external_id="aether-migrate-workspace-aaa",
            platform_account_id="123456789012",
        )
        p2 = generate_assume_role_trust_policy(
            external_id="aether-migrate-workspace-bbb",
            platform_account_id="123456789012",
        )
        ext1 = p1["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"]
        ext2 = p2["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"]
        assert ext1 != ext2
