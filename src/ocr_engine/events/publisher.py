"""Outbox -> Kafka publisher loop (port of the Go services' outbox worker)."""

from __future__ import annotations

import asyncio
import json
import socket

from confluent_kafka import Producer

from ..obs.logging import get_logger
from ..repo.store import Store

log = get_logger(__name__)

TOPIC_HEADERS = (
    "event-id",
    "event-type",
    "event-version",
    "source-service",
    "aggregate-type",
    "aggregate-id",
    "trace-id",
)


def _delivery_report(err, msg) -> None:
    if err is not None:
        log.warning("kafka delivery failed", error=str(err))


class OutboxPublisher:
    def __init__(self, store: Store, producer: Producer, topic_default: str, worker_id: str) -> None:
        self.store = store
        self.producer = producer
        self.topic_default = topic_default
        self.worker_id = worker_id

    async def poll_once(self, batch: int = 100) -> int:
        rows = await self.store.claim_pending_outbox(self.worker_id, batch)
        published = 0
        for row in rows:
            topic = row["topic"] or self.topic_default
            payload = row["payload"]
            event = json.loads(payload if isinstance(payload, (str, bytes)) else bytes(payload))
            self.producer.produce(
                topic,
                key=str(row["aggregate_id"]),
                value=payload if isinstance(payload, bytes) else str(payload).encode(),
                headers=_headers(row, event),
                on_delivery=_delivery_report,
            )
            self.producer.poll(0)
            await self.store.mark_published(row["id"])
            published += 1
        if published:
            self.producer.flush(10)
        return published

    async def run(self, stop: asyncio.Event, interval: float = 1.0) -> None:
        log.info("outbox worker started", worker_id=self.worker_id, interval=interval)
        while not stop.is_set():
            try:
                await self.poll_once()
            except Exception as exc:
                log.error("outbox publish tick failed", error=str(exc))
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
        self.producer.flush(10)
        log.info("outbox worker stopped")


def _headers(row, event: dict) -> list[tuple[str, bytes]]:
    headers: list[tuple[str, bytes]] = [
        ("event-id", str(event["event_id"]).encode()),
        ("event-type", str(event["event_type"]).encode()),
        ("event-version", b"1"),
        ("source-service", b"ocr"),
        ("aggregate-type", str(row["aggregate_type"]).encode()),
        ("aggregate-id", str(row["aggregate_id"]).encode()),
    ]
    if row["trace_id"]:
        headers.append(("trace-id", str(row["trace_id"]).encode()))
    return headers


def new_worker_id() -> str:
    import uuid

    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
