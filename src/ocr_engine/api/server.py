"""gRPC server lifecycle for the api process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import grpc
import saq

from ..api.servicer import DocumentServiceServicer
from ..obs.logging import get_logger
from ..repo.store import Store, create_pool
from ..settings import Settings

log = get_logger(__name__)


async def serve(settings: Settings, port: int) -> Callable[[], Awaitable[None]]:
    from grpc_health.v1 import health, health_pb2, health_pb2_grpc

    from ocr_engine.proto_gen.document.v1 import document_pb2_grpc

    from ..extraction.schema import load_schemas

    pool = await create_pool(settings.database_dsn, min_size=1, max_size=5)
    store = Store(pool=pool)
    schemas = load_schemas(settings.schema_dir)

    servicer = DocumentServiceServicer(store, settings, schemas)
    servicer.attach_queue(saq.Queue.from_url(settings.redis_url))

    health_servicer = health.HealthServicer()
    server = grpc.aio.server(
        options=[
            ("grpc.max_receive_message_length", 8 * 1024 * 1024),
            ("grpc.max_send_message_length", 16 * 1024 * 1024),
        ]
    )
    document_pb2_grpc.add_DocumentServiceServicer_to_server(servicer, server)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    bound = server.add_insecure_port(f"[::]:{port}")
    if bound == 0:
        raise RuntimeError(f"failed to bind gRPC port {port}")

    await server.start()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    log.info("grpc server started", port=port)

    async def stop() -> None:
        health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
        await server.grace(5)
        await store.close()
        log.info("grpc server stopped")

    return stop
