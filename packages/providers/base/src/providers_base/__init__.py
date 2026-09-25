"""AETHER MIGRATE — provider adapter protocol and supporting types."""

from providers_base.adapter import (
    AdapterCapabilities,
    CatalogBatch,
    ConnCtx,
    ConnectionTestResult,
    MetricSeries,
    ProviderAdapter,
    QuotaNeeds,
    QuotaResult,
    RawResource,
    Window,
)

__all__ = [
    "AdapterCapabilities",
    "CatalogBatch",
    "ConnCtx",
    "ConnectionTestResult",
    "MetricSeries",
    "ProviderAdapter",
    "QuotaNeeds",
    "QuotaResult",
    "RawResource",
    "Window",
]
