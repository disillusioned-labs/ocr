"""Docker health check for the OCR API's standard gRPC health service."""

from __future__ import annotations

import asyncio

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


async def main() -> None:
    async with grpc.aio.insecure_channel("127.0.0.1:9094") as channel:
        await asyncio.wait_for(channel.channel_ready(), timeout=2)
        response = await health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(),
            timeout=2,
        )
    if response.status != health_pb2.HealthCheckResponse.SERVING:
        raise SystemExit("OCR gRPC health service is not SERVING")


if __name__ == "__main__":
    asyncio.run(main())
