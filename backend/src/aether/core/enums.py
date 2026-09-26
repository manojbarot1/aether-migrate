from __future__ import annotations

from enum import StrEnum


class Provider(StrEnum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    IBM = "ibm"


class Role(StrEnum):
    """Workspace roles, ordered from least to most privileged (see PROJECT_PLAN §8.1)."""

    VIEWER = "viewer"
    ANALYST = "analyst"
    CONNECTION_ADMIN = "connection-admin"
    APPROVER = "approver"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return _ROLE_ORDER.index(self)

    def at_least(self, other: Role) -> bool:
        return self.rank >= other.rank


_ROLE_ORDER = list(Role)


class ConnectionMode(StrEnum):
    READ_ONLY = "read_only"
    EXECUTE = "execute"  # reserved for Phase 9; creation is rejected until then


class AuthMethod(StrEnum):
    AWS_ASSUME_ROLE = "aws_assume_role"
    AWS_ACCESS_KEY = "aws_access_key"


class ConnectionStatus(StrEnum):
    UNTESTED = "untested"
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class AuditStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


class ActorType(StrEnum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
