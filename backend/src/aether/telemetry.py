"""OpenTelemetry tracing. Disabled unless ``AETHER_OTEL_ENDPOINT`` is set."""

from __future__ import annotations

from typing import Any

from aether.config import Settings


def setup_tracing(settings: Settings, app: Any | None = None) -> None:
    if not settings.otel_endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": settings.version,
                "deployment.environment": settings.env,
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # Health probes would drown out real traffic.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="livez,readyz")
