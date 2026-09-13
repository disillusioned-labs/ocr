"""SAQ worker settings: one process, one context, two concurrency layers."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import httpx
from saq import CronJob, Queue
from saq.types import SettingsDict

from ..obs.logging import get_logger
from ..ocr.registry import build_provider
from ..pipeline.pipeline import process_document
from ..repo.store import Store, create_pool
from ..settings import Settings, get_settings

log = get_logger(__name__)

RESULT_RETENTION = dt.timedelta(hours=24)


async def process_document_job(ctx: dict[str, Any], *, document_id: str) -> None:
    await process_document(ctx, document_id)


async def cleanup_expired_results(ctx: dict[str, Any]) -> None:
    """Retention sweep: purge result payloads older than the retention window."""
    store: Store = ctx["store"]
    cutoff = dt.datetime.now(dt.UTC) - RESULT_RETENTION
    ids = await store.list_expired_documents(cutoff)
    if ids:
        await store.purge_document_results(ids)
        log.info("purged expired document results", count=len(ids))


async def _startup(ctx: dict[str, Any]) -> None:
    settings: Settings = get_settings()
    store = Store(pool=await create_pool(settings.database_dsn, max_size=settings.worker_concurrency))
    http = httpx.AsyncClient(timeout=settings.baidu_timeout)
    from ..extraction.schema import load_schemas

    ctx.update(
        settings=settings,
        store=store,
        http=http,
        schemas=load_schemas(settings.schema_dir),
        provider=build_provider(settings, http),
        ocr_semaphore=asyncio.Semaphore(settings.ocr_semaphore),
    )
    log.info(
        "worker started",
        provider=settings.provider.value,
        concurrency=settings.worker_concurrency,
        ocr_semaphore=settings.ocr_semaphore,
    )


async def _shutdown(ctx: dict[str, Any]) -> None:
    await ctx["store"].close()
    await ctx["http"].aclose()


def saq_settings(settings: Settings | None = None) -> SettingsDict:
    settings = settings or get_settings()
    return SettingsDict(
        queue=Queue.from_url(settings.redis_url),
        functions=[process_document_job],
        cron_jobs=[CronJob(cleanup_expired_results, cron="*/30 * * * *")],
        concurrency=settings.worker_concurrency,
        max_tries=3,
        job_timeout=300,
        heartbeat=15,
        startup=[_startup],
        shutdown=[_shutdown],
    )
