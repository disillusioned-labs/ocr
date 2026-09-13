"""Metrics contract: instruments record and export under a real provider."""

from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource

from ocr_engine.obs.metrics import (
    DOCUMENTS_PROCESSED,
    OUTBOX_PUBLISHED,
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
            "ocr.outbox.published",
        } <= names
    finally:
        metrics.set_meter_provider(old)
