"""Optional OTLP tracing push, mirroring the Go services' telemetry pattern."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(endpoint: str | None, service_name: str = "ocr", env: str = "development") -> None:
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "deployment.environment": env})
    )
    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://")))
        )
    else:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter(out=None)))
    trace.set_tracer_provider(provider)


def tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
