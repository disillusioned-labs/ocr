"""Metrics contract: instruments record and export under a real provider."""

from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource

from ocr_engine.obs.metrics import (
    DOCUMENT_FIELDS,
    DOCUMENTS_PROCESSED,
    OCR_PROVIDER_DURATION,
    OUTBOX_PUBLISHED,
    QUEUE_WAIT,
    RPC_DURATION,
    RPC_REQUESTS,
)


def test_instruments_export_with_expected_names() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(resource=Resource.create({"service.name": "ocr"}), metric_readers=[reader])
    from opentelemetry import metrics

    old = metrics.get_meter_provider()
    metrics.set_meter_provider(provider)
    try:
        RPC_REQUESTS.add(1, {"rpc_method": "SubmitDocument", "rpc_grpc_status": "OK"})
        RPC_DURATION.record(0.42, {"rpc_method": "SubmitDocument"})
        DOCUMENTS_PROCESSED.add(2, {"status": "completed"})
        DOCUMENT_FIELDS.add(1, {"field": "total", "status": "extracted"})
        OCR_PROVIDER_DURATION.record(12.5, {"ocr_provider": "baidu_aistudio"})
        QUEUE_WAIT.record(0.3)
        OUTBOX_PUBLISHED.add(1, {"topic": "ocr.document.processed.v1"})

        data = reader.get_metrics_data()
        names = {
            resource_metric.name
            for resource_metric in data.resource_metrics[0].scope_metrics[0].metrics
        }
        assert {
            "ocr.rpc.requests",
            "ocr.rpc.duration",
            "ocr.documents.processed",
            "ocr.document.fields",
            "ocr.provider.duration",
            "ocr.pipeline.queue_wait",
            "ocr.outbox.published",
        } <= names
    finally:
        metrics.set_meter_provider(old)
