"""outbox process: poll outbox_events and publish to Kafka."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from confluent_kafka import Producer

from ..events.publisher import OutboxPublisher, new_worker_id
from ..obs.logging import configure_logging
from ..obs.metrics import setup_metrics
from ..obs.tracing import setup_tracing
from ..repo.store import Store, create_pool
from ..settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    setup_tracing(
        settings.otel_endpoint,
        sdk_disabled=settings.otel_sdk_disabled,
        service_name=settings.service_name,
        service_env=settings.service_env,
    )
    setup_metrics(
        settings.otel_endpoint,
        sdk_disabled=settings.otel_sdk_disabled,
        interval_ms=settings.metric_interval_ms,
        service_name=settings.service_name,
        service_env=settings.service_env,
    )

    async def run() -> None:
        store = Store(pool=await create_pool(settings.database_dsn, min_size=1, max_size=4))
        producer = Producer(
            {
                "bootstrap.servers": settings.kafka_brokers,
                "client.id": "ocr-outbox",
                "enable.idempotence": True,
                "acks": "all",
            }
        )
        publisher = OutboxPublisher(store, producer, settings.kafka_topic, new_worker_id())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        try:
            await publisher.run(stop)
        finally:
            await store.close()

    asyncio.run(run())
