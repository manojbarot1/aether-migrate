"""AWS normalizers — pure functions that convert raw boto3 responses to domain models.

No network I/O, no DB calls. Each function is deterministic and can be called
multiple times with the same input to produce the same output.
"""

from aws.normalizers.disk import normalize_ebs_volume
from aws.normalizers.network import normalize_subnet, normalize_vpc
from aws.normalizers.security_group import normalize_security_group
from aws.normalizers.vm import normalize_ec2_instance

__all__ = [
    "normalize_ec2_instance",
    "normalize_ebs_volume",
    "normalize_vpc",
    "normalize_subnet",
    "normalize_security_group",
]
