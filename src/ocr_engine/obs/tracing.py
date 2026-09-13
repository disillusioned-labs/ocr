"""Optional OTLP tracing push, mirroring platform/telemetry in the Go services."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(
    endpoint: str | None,
    *,
    sdk_disabled: bool = False,
    service_name: str = "ocr",
    service_env: str = "development",
) -> None:
    """Install the process-wide TracerProvider.

    Off means off: with no endpoint or OTEL_SDK_DISABLED=true the SDK is never
    installed, so spans are no-ops and instrumentation costs nothing - the
    same disabled-by-default contract as the Go services.
    """
    if sdk_disabled or not endpoint:
        return

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name, "deployment.environment": service_env}
        )
    )
    # The scheme selects TLS, mirroring the Go services: http:// is the local
    # collector, https:// the production one.
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
        )
    )
    trace.set_tracer_provider(provider)


def tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
