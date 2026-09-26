from __future__ import annotations


class AetherError(Exception):
    """Base error. ``public_message`` is safe to show to users; never include secrets."""

    status_code = 500
    code = "internal_error"

    def __init__(self, public_message: str = "Internal error") -> None:
        super().__init__(public_message)
        self.public_message = public_message


class NotFoundError(AetherError):
    status_code = 404
    code = "not_found"


class ForbiddenError(AetherError):
    status_code = 403
    code = "forbidden"


class ConflictError(AetherError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AetherError):
    status_code = 422
    code = "validation_failed"


class UpstreamError(AetherError):
    status_code = 502
    code = "upstream_error"
