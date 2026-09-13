"""api process: run the gRPC server until a signal arrives."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from ..api.server import serve
from ..obs.logging import configure_logging
from ..settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.service_name, settings.service_env)
    stop = asyncio.Event()

    async def run() -> None:
        shutdown = await serve(settings, settings.grpc_port)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await shutdown()

    asyncio.run(run())
