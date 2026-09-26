"""Least-privilege policy templates shown to users when they set up a connection.

Pure data: no SDK imports, so the API can render these without touching the
credential boundary. Keep in sync with what the provider adapters actually call;
``tests/unit/test_policies.py`` checks that every operation the AWS adapter uses
is covered.
"""

from __future__ import annotations

from typing import Any

# Read-only discovery permissions (PROJECT_PLAN §8.2). Deliberately not the AWS
# managed ReadOnlyAccess policy, which also allows reading S3 object data.
AWS_DISCOVERY_ACTIONS: tuple[str, ...] = (
    "sts:GetCallerIdentity",
    "ec2:DescribeRegions",
    "ec2:DescribeAvailabilityZones",
    "ec2:DescribeInstances",
    "ec2:DescribeInstanceTypes",
    "ec2:DescribeImages",
    "ec2:DescribeVolumes",
    "ec2:DescribeSnapshots",
    "ec2:DescribeNetworkInterfaces",
    "ec2:DescribeSubnets",
    "ec2:DescribeVpcs",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeSecurityGroupRules",
    "ec2:DescribeRouteTables",
    "ec2:DescribeInternetGateways",
    "ec2:DescribeNatGateways",
    "ec2:DescribeAddresses",
    "ec2:DescribeTags",
    "elasticloadbalancing:DescribeLoadBalancers",
    "elasticloadbalancing:DescribeTargetGroups",
    "elasticloadbalancing:DescribeTargetHealth",
    "elasticloadbalancing:DescribeListeners",
    "cloudwatch:GetMetricData",
    "cloudwatch:ListMetrics",
    "pricing:GetProducts",
    "ce:GetCostAndUsage",
    "servicequotas:GetServiceQuota",
    "servicequotas:ListServiceQuotas",
)

# Actions whose presence on a *read-only* connection is flagged as excess privilege.
AWS_EXCESS_PROBE_ACTIONS: tuple[str, ...] = (
    "ec2:RunInstances",
    "ec2:TerminateInstances",
    "ec2:StopInstances",
    "ec2:ModifyInstanceAttribute",
    "ec2:CreateSnapshot",
    "ec2:DeleteVolume",
    "ec2:AuthorizeSecurityGroupIngress",
    "iam:CreateUser",
    "iam:AttachRolePolicy",
    "iam:PassRole",
    "s3:GetObject",
    "s3:PutObject",
    "kms:Decrypt",
    "secretsmanager:GetSecretValue",
    "ssm:SendCommand",
)


def aws_permissions_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AetherMigrateReadOnlyDiscovery",
                "Effect": "Allow",
                "Action": list(AWS_DISCOVERY_ACTIONS),
                "Resource": "*",
            },
            {
                # Lets the platform verify the role has no excess permissions.
                # Scoped to the role itself; replace ROLE_NAME.
                "Sid": "AetherMigrateSelfSimulation",
                "Effect": "Allow",
                "Action": "iam:SimulatePrincipalPolicy",
                "Resource": "arn:aws:iam::*:role/ROLE_NAME",
            },
        ],
    }


def aws_trust_policy(platform_principal_arn: str | None, external_id: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": platform_principal_arn or "<AETHER_PLATFORM_PRINCIPAL_ARN>"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
            }
        ],
    }
