"""Provider adapter contract (PROJECT_PLAN §23).

Adapters run only inside connector workers. They receive secret material as
in-memory values for the duration of one call and must never log, persist or
return it. Every adapter must pass ``tests/unit/providers/contract``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from aether.core.connections import ConnectionTestResult
from aether.core.enums import Provider


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    metrics: bool = False
    actual_cost: bool = False
    boot_mode: bool = False
    native_dry_run: bool = False
    permission_simulation: bool = False


@dataclass(slots=True)
class ConnCtx:
    """Everything an adapter needs to authenticate. Lives only in worker memory."""

    connection_id: str
    auth_method: str
    config: Mapping[str, Any]
    secret: Mapping[str, str] | None = field(default=None, repr=False)
    platform_secret: Mapping[str, str] | None = field(default=None, repr=False)


class ProviderAdapter(Protocol):
    provider: Provider
    capabilities: AdapterCapabilities

    def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult: ...
