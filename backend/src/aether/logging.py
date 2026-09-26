"""Structured logging with secret redaction.

Redaction is defence in depth: code should never log secrets in the first place,
but if a credential slips into an event dict or message it is masked here.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "***REDACTED***"

# Keys whose values are always masked, matched case-insensitively as substrings.
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "session_key",
    "client_assertion",
)

# Patterns for well-known credential formats that might appear inside free text.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),  # AWS access key id
    re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(hvs|s|b)\.[A-Za-z0-9]{20,}\b"),  # OpenBao/Vault tokens
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_text(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a copy of ``value`` with sensitive keys and secret-looking strings masked."""
    if _depth > 20:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive_key(k) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v, _depth + 1) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def _redaction_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return redact(dict(event_dict))  # type: ignore[no-any-return]


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redaction_processor,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level.upper()]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging (uvicorn, temporal, sqlalchemy) through the same redaction.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
            foreign_pre_chain=shared,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "botocore.endpoint", "botocore.regions"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
