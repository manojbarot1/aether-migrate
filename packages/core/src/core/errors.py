"""Domain exceptions for AETHER MIGRATE."""

from __future__ import annotations


class AetherError(Exception):
    """Base exception for all AETHER MIGRATE domain errors."""

    def __init__(self, message: str, code: str = "aether_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"


class NotFoundError(AetherError):
    """Raised when a requested resource does not exist."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(
            message=f"{resource_type} '{resource_id}' not found",
            code="not_found",
        )
        self.resource_type = resource_type
        self.resource_id = resource_id


class AuthorizationError(AetherError):
    """Raised when the caller lacks permission to perform an action."""

    def __init__(self, action: str, required_role: str | None = None) -> None:
        msg = f"Not authorized to perform '{action}'"
        if required_role:
            msg += f" (requires role: {required_role})"
        super().__init__(message=msg, code="unauthorized")
        self.action = action
        self.required_role = required_role


class ConnectionError(AetherError):
    """Raised when a cloud provider connection cannot be established or tested."""

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(
            message=f"Connection to '{provider}' failed: {detail}",
            code="connection_error",
        )
        self.provider = provider
        self.detail = detail


class DiscoveryError(AetherError):
    """Raised when resource discovery encounters an unrecoverable error."""

    def __init__(self, provider: str, region: str, detail: str) -> None:
        super().__init__(
            message=f"Discovery failed for {provider}/{region}: {detail}",
            code="discovery_error",
        )
        self.provider = provider
        self.region = region
        self.detail = detail


class ValidationError(AetherError):
    """Raised when data fails domain-level validation (distinct from Pydantic)."""

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(
            message=f"Validation error on field '{field}': {detail}",
            code="validation_error",
        )
        self.field = field
        self.detail = detail
