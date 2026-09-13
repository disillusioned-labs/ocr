"""worker process: SAQ worker running the pipeline jobs."""

from __future__ import annotations

import asyncio
import logging

from saq.worker import Worker

from ..obs.logging import configure_logging
from ..obs.metrics import set_process_role, setup_metrics
from ..obs.tracing import setup_tracing
from ..settings import get_settings
from ..worker.settings import saq_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    setup_tracing(
        settings.otel_endpoint,
        sdk_disabled=settings.otel_sdk_disabled,
        service_name=settings.service_name,
        service_env=settings.service_env,
    )
    set_process_role("worker")
    setup_metrics(
        settings.otel_endpoint,
        sdk_disabled=settings.otel_sdk_disabled,
        interval_ms=settings.metric_interval_ms,
        service_name=settings.service_name,
        service_env=settings.service_env,
    )
    logging.getLogger("saq").setLevel(logging.WARNING)

    async def run() -> None:
        worker = Worker(saq_settings(settings))
        await worker.queue.connect()
        try:
            # start() runs until stop() or the process dies; a killed worker's
            # jobs recover via the SAQ heartbeat sweep, so no extra signal
            # dance is needed here.
            await worker.start()
        finally:
            await worker.queue.disconnect()

    asyncio.run(run())
