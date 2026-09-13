"""Stage orchestration: download -> inspect -> branch -> OCR -> extract -> validate.

Terminal outcomes land through repo.finish_document so the document row and
its outbox event commit together - there is no world where the result is
stored but its event is not written.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..events.envelope import build_envelope
from ..extraction.extract import extract_fields
from ..extraction.schema import Schema
from ..extraction.validate import build_result
from ..obs.logging import get_logger
from ..obs.tracing import tracer
from ..ocr.baidu import ProviderPermanentError, ProviderTransientError
from ..ocr.base import Line
from ..repo.store import DocumentRow, Store
from ..settings import Settings
from . import download, inspect_file

log = get_logger(__name__)


class DocumentFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


async def process_document(ctx: dict[str, Any], document_id: str) -> None:
    store: Store = ctx["store"]
    settings: Settings = ctx["settings"]
    schemas: dict[str, Schema] = ctx["schemas"]
    provider = ctx["provider"]
    semaphore = ctx["ocr_semaphore"]

    doc = await store.get_document(uuid.UUID(document_id))
    if doc is None:
        log.warning("document not found; skipping", document_id=document_id)
        return
    if doc.status not in ("queued", "processing"):
        log.info("document not in a processable state; skipping", document_id=document_id, status=doc.status)
        return

    await store.mark_processing(doc.id)

    with tracer("pipeline.process").start_as_current_span("process_document") as span:
        span.set_attribute("ocr.document_id", document_id)
        span.set_attribute("ocr.schema_id", doc.doc_type)
        tmp_path = None
        try:
            try:
                tmp_path = await download.download_to_temp(settings, doc.source_bucket, doc.source_path)
                content = tmp_path.read_bytes()
            except download.StorageObjectMissing as exc:
                raise DocumentFailed("STORAGE_OBJECT_MISSING", str(exc)) from None
            except ValueError as exc:
                raise DocumentFailed("RESOURCE_LIMIT", str(exc)) from None

            meta = _inspect(content, settings)

            lines = inspect_file.digitalborn_lines(content)
            if lines is None:
                lines = await _ocr_lines(provider, content, meta.mime, settings, semaphore)

            schema = schemas.get(doc.doc_type)
            if schema is None:
                raise DocumentFailed("INTERNAL", f"schema {doc.doc_type!r} not loaded")
            extracted = extract_fields(schema, lines)
            result, status = build_result(schema, extracted)

            await store.finish_document(
                doc.id,
                status=status,
                result=result.to_dict(),
                avg_confidence=result.avg_confidence,
                error_code=None,
                error_message=None,
                payload=build_envelope(doc, status=status, result=result.to_dict(), error=None),
                topic=settings.kafka_topic,
                trace_id=doc.trace_id,
            )
            log.info(
                "document processed",
                document_id=document_id,
                status=status,
                fields=len(result.fields),
                avg_confidence=round(result.avg_confidence, 3),
            )

        except DocumentFailed as exc:
            await _fail(store, doc, settings, exc.code, exc.message)
        except ProviderTransientError as exc:
            raise _TransientJobError(str(exc)) from exc
        except ProviderPermanentError as exc:
            await _fail(store, doc, settings, "INTERNAL", str(exc))
        except inspect_file.UnsupportedFileType as exc:
            await _fail(store, doc, settings, "UNSUPPORTED_FILE_TYPE", str(exc))
        except inspect_file.ResourceLimit as exc:
            await _fail(store, doc, settings, "RESOURCE_LIMIT", str(exc))
        except Exception as exc:
            await _fail(store, doc, settings, "INTERNAL", str(exc))
            raise
        finally:
            download.cleanup_temp(tmp_path)


class _TransientJobError(Exception):
    """Re-raised so SAQ retries the whole job; the document stays processing."""


def _inspect(content: bytes, settings: Settings):
    try:
        return inspect_file.inspect(content, settings)
    except inspect_file.UnsupportedFileType as exc:
        raise DocumentFailed("UNSUPPORTED_FILE_TYPE", str(exc)) from None
    except inspect_file.ResourceLimit as exc:
        raise DocumentFailed("RESOURCE_LIMIT", str(exc)) from None


async def _ocr_lines(provider, content: bytes, mime: str, settings: Settings, semaphore) -> list[Line]:

    async with semaphore:
        if mime == "application/pdf":
            images = inspect_file.pdf_page_images(content, settings)
            lines: list[Line] = []
            for image in images:
                lines.extend(await provider.extract_lines(image, "image/jpeg"))
            return lines
        return await provider.extract_lines(inspect_file.downscale_if_needed(content, settings), mime)


async def _fail(store: Store, doc: DocumentRow, settings: Settings, code: str, message: str) -> None:
    await store.finish_document(
        doc.id,
        status="failed",
        result=None,
        avg_confidence=None,
        error_code=code,
        error_message=message,
        payload=build_envelope(
            doc,
            status="failed",
            result=None,
            error={"code": code, "message": message},
        ),
        topic=settings.kafka_topic,
        trace_id=doc.trace_id,
    )
    log.warning("document failed", document_id=str(doc.id), error_code=code)
