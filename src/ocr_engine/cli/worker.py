"""worker process: SAQ worker running the pipeline jobs."""

from __future__ import annotations

import logging

from saq.worker import Worker, run_worker

from ..obs.logging import configure_logging
from ..settings import get_settings
from ..worker.settings import saq_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.service_name, settings.service_env)
    logging.getLogger("saq").setLevel(logging.WARNING)

    worker = Worker(saq_settings(settings))
    run_worker(worker)
