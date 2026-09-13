"""OTel metrics: instruments built once here, pushed over OTLP to the central
collector - the same pattern as the Go services (library- or process-level
metrics are never scraped; there is no /metrics endpoint to widen).

Instrument names are the Prometheus contract of this service (the collector's
remotewrite exporter translates them): `ocr.rpc.requests` lands as
`ocr_rpc_requests_total`, `ocr.pipeline.duration` (unit s) as
`ocr_pipeline_duration_seconds_*`. Labels stay bounded - status, error_code,
provider and method come from fixed sets; nothing carries a document id or
any OCR text.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("ocr_engine")

RPC_REQUESTS = _meter.create_counter(
    "ocr.rpc.requests",
    unit="{request}",
    description="gRPC requests by method and terminal status",
)
RPC_DURATION = _meter.create_histogram(
    "ocr.rpc.duration",
    unit="s",
    description="gRPC handler duration by method",
)
DOCUMENTS_PROCESSED = _meter.create_counter(
    "ocr.documents.processed",
    unit="{document}",
    description="Documents reaching a terminal status (completed/needs_review/failed)",
)
DOCUMENTS_FAILED = _meter.create_counter(
    "ocr.documents.failed",
    unit="{document}",
    description="Failed documents by error code",
)
PIPELINE_DURATION = _meter.create_histogram(
    "ocr.pipeline.duration",
    unit="s",
    description="End-to-end processing duration per document",
)
OCR_LINES = _meter.create_histogram(
    "ocr.document.lines",
    unit="{line}",
    description="OCR lines read per document",
)
OCR_PAGES = _meter.create_counter(
    "ocr.provider.pages",
    unit="{page}",
    description="Pages sent to the OCR provider - the daily Baidu quota budget",
)
OCR_PROVIDER_DURATION = _meter.create_histogram(
    "ocr.provider.duration",
    unit="s",
    description="OCR-stage (provider call) duration per document",
)
DOCUMENT_CONFIDENCE = _meter.create_histogram(
    "ocr.document.confidence",
    description="Average result confidence per document (0-1)",
)
DOCUMENT_FIELDS = _meter.create_counter(
    "ocr.document.fields",
    unit="{field}",
    description="Field outcomes per extraction schema field (merchant/date/total/...)",
)
DOCUMENT_SIZE = _meter.create_histogram(
    "ocr.document.size",
    unit="By",
    description="Downloaded document size",
)
QUEUE_WAIT = _meter.create_histogram(
    "ocr.pipeline.queue_wait",
    unit="s",
    description="Time from submit (created_at) to processing start",
)
TRANSIENT_ERRORS = _meter.create_counter(
    "ocr.pipeline.transient_errors",
    unit="{error}",
    description="Transient provider failures - SAQ retries the job",
)
PROVIDER_ERRORS = _meter.create_counter(
    "ocr.provider.errors",
    unit="{error}",
    description="Provider failures by class (transient/permanent)",
)
OUTBOX_CLAIMED = _meter.create_gauge(
    "ocr.outbox.claimed",
    unit="{event}",
    description="Outbox rows claimed per publisher poll (saturated at the batch size)",
)
OUTBOX_PUBLISHED = _meter.create_counter(
    "ocr.outbox.published",
    unit="{event}",
    description="Outbox events acknowledged by Kafka",
)
OUTBOX_FAILED = _meter.create_counter(
    "ocr.outbox.publish_failed",
    unit="{event}",
    description="Outbox events whose Kafka delivery failed",
)
# Push telemetry has no `up`: this constant gauge is the liveness signal -
# `absent(ocr_process_running)` in a rules file means the process stopped
# exporting, the same role go_goroutine_count plays for the Go services.
_PROCESS_ROLE = "api"
PROCESS_RUNNING = _meter.create_observable_gauge(
    "ocr.process.running",
    callbacks=[lambda _options: [metrics.Observation(1, {"ocr_process": _PROCESS_ROLE})]],
    description="Always 1 while the process lives; absence = process gone",
)


def set_process_role(role: str) -> None:
    """Label the liveness gauge with the binary role (api/worker/outbox)."""
    global _PROCESS_ROLE
    _PROCESS_ROLE = role

PROVIDER_ATTR = "ocr_provider"


def setup_metrics(
    endpoint: str | None,
    *,
    sdk_disabled: bool = False,
    interval_ms: int = 15_000,
    service_name: str = "ocr",
    service_env: str = "development",
) -> None:
    """Install the process-wide MeterProvider.

    Off means off: with no endpoint or OTEL_SDK_DISABLED=true no reader is
    installed, so recording costs nothing - the instruments above stay API
    no-ops, mirroring the Go services' disabled telemetry.
    """
    if sdk_disabled or not endpoint:
        return

    import socket

    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    provider = MeterProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": service_env,
                # Push has no scrape target address to derive instance from:
                # without this, every replica collapses onto one series.
                "service.instance.id": socket.gethostname(),
            }
        ),
        metric_readers=[
            PeriodicExportingMetricReader(
                # The scheme selects TLS, mirroring the Go services.
                OTLPMetricExporter(endpoint=endpoint, insecure=endpoint.startswith("http://")),
                export_interval_millis=interval_ms,
            )
        ],
    )
    metrics.set_meter_provider(provider)
