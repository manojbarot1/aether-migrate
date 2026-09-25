"""AWS IAM policy generator for AETHER MIGRATE.

Provides the minimal IAM policies required for cloud-resource discovery:
- ``generate_discovery_policy()``      — inline policy to attach to the discovery role.
- ``generate_assume_role_trust_policy()`` — trust policy for cross-account role assumption.

The generated policies follow the principle of least-privilege:
- No s3:* or iam:* actions are granted.
- Only read (Describe*/List*/Get*) actions are included.
- ec2:TerminateInstances and all write actions are explicitly absent.
"""

from __future__ import annotations

from typing import Any


def generate_discovery_policy() -> dict[str, Any]:
    """Return the minimal IAM policy JSON for cloud-resource discovery.

    Covers:
    - EC2 Describe* (instances, volumes, networking, security groups)
    - ELB / ELBv2 Describe*
    - CloudWatch GetMetricData / GetMetricStatistics
    - AWS Price List pricing:GetProducts
    - Cost Explorer ce:GetCostAndUsage  (optional — grants billing read)
    - STS sts:GetCallerIdentity  (required for connection test)

    Explicitly excluded:
    - s3:* (no object-store access)
    - iam:* (no identity and access management read)
    - ec2:Terminate* / ec2:Stop* / ec2:Start* (no instance control)
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EC2DiscoveryReadOnly",
                "Effect": "Allow",
                "Action": [
                    "ec2:DescribeInstances",
                    "ec2:DescribeInstanceTypes",
                    "ec2:DescribeInstanceStatus",
                    "ec2:DescribeVolumes",
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeRouteTables",
                    "ec2:DescribeInternetGateways",
                    "ec2:DescribeRegions",
                    "ec2:DescribeAvailabilityZones",
                    "ec2:DescribeTags",
                    "ec2:DescribeSnapshots",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ELBDiscoveryReadOnly",
                "Effect": "Allow",
                "Action": [
                    "elasticloadbalancing:DescribeLoadBalancers",
                    "elasticloadbalancing:DescribeTargetGroups",
                    "elasticloadbalancing:DescribeListeners",
                    "elasticloadbalancing:DescribeTags",
                ],
                "Resource": "*",
            },
            {
                "Sid": "CloudWatchMetricsReadOnly",
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:GetMetricData",
                    "cloudwatch:GetMetricStatistics",
                    "cloudwatch:ListMetrics",
                ],
                "Resource": "*",
            },
            {
                "Sid": "PricingReadOnly",
                "Effect": "Allow",
                "Action": [
                    "pricing:GetProducts",
                    "pricing:DescribeServices",
                ],
                "Resource": "*",
            },
            {
                "Sid": "CostExplorerReadOnly",
                "Effect": "Allow",
                "Action": [
                    "ce:GetCostAndUsage",
                    "ce:GetCostForecast",
                ],
                "Resource": "*",
            },
            {
                "Sid": "STSCallerIdentity",
                "Effect": "Allow",
                "Action": [
                    "sts:GetCallerIdentity",
                ],
                "Resource": "*",
            },
        ],
    }


def generate_assume_role_trust_policy(
    external_id: str,
    platform_account_id: str,
) -> dict[str, Any]:
    """Return the IAM trust policy for the cross-account discovery role.

    The trust policy allows only the AETHER MIGRATE platform account to assume
    the role, and requires the ``ExternalId`` condition to prevent confused-deputy
    attacks.

    Parameters
    ----------
    external_id:
        The workspace-specific external ID string. Should be in the form
        ``aether-migrate-{workspace_id}`` to ensure uniqueness per workspace.
    platform_account_id:
        The AWS account ID of the AETHER MIGRATE platform (12 digits).
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AetherMigratePlatformTrust",
                "Effect": "Allow",
                "Principal": {
                    "AWS": f"arn:aws:iam::{platform_account_id}:root",
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "sts:ExternalId": external_id,
                    }
                },
            }
        ],
    }
