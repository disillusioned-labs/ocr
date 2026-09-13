"""Kontrak B servicer: the validation order is part of the contract (api-ocr.md)."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import grpc
from opentelemetry import trace

from ocr_engine.proto_gen.document.v1 import document_pb2, document_pb2_grpc

from ..events.envelope import build_envelope
from ..obs.logging import get_logger
from ..obs.tracing import tracer
from ..pipeline.pipeline import process_document
from ..repo.store import Store
from ..settings import Settings

log = get_logger(__name__)

STATUS_MAP = {
    "queued": document_pb2.DOCUMENT_STATUS_QUEUED,
    "processing": document_pb2.DOCUMENT_STATUS_PROCESSING,
    "completed": document_pb2.DOCUMENT_STATUS_COMPLETED,
    "needs_review": document_pb2.DOCUMENT_STATUS_NEEDS_REVIEW,
    "failed": document_pb2.DOCUMENT_STATUS_FAILED,
}

FIELD_STATUS_MAP = {
    "extracted": document_pb2.FIELD_STATUS_EXTRACTED,
    "low_confidence": document_pb2.FIELD_STATUS_LOW_CONFIDENCE,
    "missing": document_pb2.FIELD_STATUS_MISSING,
    "invalid": document_pb2.FIELD_STATUS_INVALID,
}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
ALLOWED_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/tiff",
    "image/bmp",
    "image/webp",
    "application/pdf",
}


class _Invalid(Exception):
    pass


class DocumentServiceServicer(document_pb2_grpc.DocumentServiceServicer):
    def __init__(
        self, store: Store, settings: Settings, schemas: dict, pipeline_ctx: dict | None = None
    ) -> None:
        self.store = store
        self.settings = settings
        self.schemas = schemas
        self._queue = None
        self._pipeline_ctx = pipeline_ctx

    def attach_queue(self, queue) -> None:
        self._queue = queue

    async def SubmitDocument(self, request, context):
        with tracer("api.submit").start_as_current_span("SubmitDocument") as span:
            try:
                self._validate_submit(request, span)
            except _Invalid as exc:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

            document_id = uuid.uuid4()
            inserted = await self.store.submit_document(
                document_id=document_id,
                idempotency_key=request.idempotency_key,
                external_ref=request.external_ref,
                schema_id=request.schema_id,
                caller_id=request.caller_id,
                source_bucket=request.source.bucket,
                source_path=request.source.storage_path,
                declared_mime=request.source.declared_mime,
                size_bytes=request.source.size_bytes,
                trace_id=_trace_id(),
            )
            if not inserted:
                existing = await self.store.get_idempotent_document(request.idempotency_key)
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    f"document_id={existing.id if existing else 'unknown'}",
                )

            try:
                await self._queue.enqueue("process_document_job", document_id=str(document_id))
            except Exception as exc:
                await self._rollback(document_id)
                log.error("enqueue failed; submit rolled back", error=str(exc))
                await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "queue unavailable")

            span.set_attribute("ocr.document_id", str(document_id))
            return document_pb2.SubmitDocumentResponse(
                document_id=str(document_id), status=document_pb2.DOCUMENT_STATUS_QUEUED
            )

    async def ProcessDocument(self, request, context):
        with tracer("api.submit").start_as_current_span("ProcessDocument") as span:
            try:
                self._validate_submit(request, span)
            except _Invalid as exc:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            if request.source.size_bytes > self.settings.sync_max_bytes:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"size_bytes {request.source.size_bytes} exceeds the synchronous cap "
                    f"({self.settings.sync_max_bytes}); submit via SubmitDocument instead",
                )
            if self._pipeline_ctx is None:
                await context.abort(grpc.StatusCode.UNAVAILABLE, "processing not configured")

            document_id = uuid.uuid4()
            inserted = await self.store.submit_document(
                document_id=document_id,
                idempotency_key=request.idempotency_key,
                external_ref=request.external_ref,
                schema_id=request.schema_id,
                caller_id=request.caller_id,
                source_bucket=request.source.bucket,
                source_path=request.source.storage_path,
                declared_mime=request.source.declared_mime,
                size_bytes=request.source.size_bytes,
                trace_id=_trace_id(),
            )
            if not inserted:
                # Idempotent sync: a repeated key answers the document's
                # current state instead of re-processing (or ALREADY_EXISTS).
                existing = await self.store.get_idempotent_document(request.idempotency_key)
                if existing is None:
                    await context.abort(grpc.StatusCode.INTERNAL, "idempotent document vanished")
                span.set_attribute("ocr.document_id", str(existing.id))
                span.set_attribute("ocr.idempotent_replay", True)
                return self._response_for(existing)

            try:
                await process_document(self._pipeline_ctx, str(document_id))
            except asyncio.CancelledError:
                # The caller gave up mid-OCR. Nothing will ever finish this
                # document (no SAQ job exists to sweep it), so mark it failed
                # instead of leaving a processing row that never resolves.
                await self._mark_cancelled(document_id)
                raise
            except Exception as exc:
                log.error("inline processing failed", document_id=str(document_id), error=str(exc))
                await context.abort(grpc.StatusCode.INTERNAL, "processing failed")

            doc = await self.store.get_document(document_id)
            if doc is None:
                await context.abort(grpc.StatusCode.INTERNAL, "document vanished after processing")
            span.set_attribute("ocr.document_id", str(document_id))
            span.set_attribute("ocr.status", doc.status)
            return self._response_for(doc)

    async def _mark_cancelled(self, document_id: uuid.UUID) -> None:
        try:
            doc = await self.store.get_document(document_id)
            if doc is None:
                return
            await self.store.finish_document(
                document_id,
                status="failed",
                result=None,
                avg_confidence=None,
                error_code="INTERNAL",
                error_message="synchronous processing cancelled before completion",
                payload=build_envelope(
                    doc,
                    status="failed",
                    result=None,
                    error={
                        "code": "INTERNAL",
                        "message": "synchronous processing cancelled before completion",
                    },
                ),
                topic=self.settings.kafka_topic,
                trace_id=_trace_id(),
            )
        except Exception:
            log.error("failed to mark cancelled document", document_id=str(document_id))

    def _response_for(self, doc) -> document_pb2.GetDocumentResponse:
        response = document_pb2.GetDocumentResponse(document_id=str(doc.id), status=STATUS_MAP[doc.status])
        if doc.status in ("completed", "needs_review") and doc.result is not None:
            _fill_result(response.result, doc.result)
        if doc.status == "failed":
            response.error.code = doc.error_code or "INTERNAL"
            response.error.message = doc.error_message or ""
        return response

    async def GetDocument(self, request, context):
        try:
            document_id = uuid.UUID(request.document_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "document_id is not a UUID")

        doc = await self.store.get_document(document_id)
        if doc is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "document not found")
        return self._response_for(doc)

    async def _rollback(self, document_id: uuid.UUID) -> None:
        try:
            await self.store.pool.execute("DELETE FROM documents WHERE id = $1", document_id)
        except Exception:
            log.error("rollback after enqueue failure failed", document_id=str(document_id))

    def _validate_submit(self, request, span) -> None:
        if not _UUID_RE.match(request.idempotency_key or ""):
            raise _Invalid("idempotency_key must be a UUID")
        if not request.external_ref:
            raise _Invalid("external_ref must not be empty")
        if request.schema_id not in self.schemas:
            raise _Invalid(f"unknown schema_id {request.schema_id!r}")
        if not request.caller_id:
            raise _Invalid("caller_id must not be empty")

        source = request.source
        if not source.bucket or not source.storage_path:
            raise _Invalid("source.bucket and source.storage_path are required")
        if source.bucket not in self.settings.allowed_buckets:
            raise _Invalid(f"bucket {source.bucket!r} is not in the allowlist")
        if ".." in Path(source.storage_path).parts or source.storage_path.startswith("/"):
            raise _Invalid(f"invalid storage_path {source.storage_path!r}")
        if source.size_bytes <= 0 or source.size_bytes > self.settings.max_file_bytes:
            raise _Invalid(f"size_bytes out of range (1..{self.settings.max_file_bytes})")
        if source.declared_mime and source.declared_mime not in ALLOWED_MIMES:
            raise _Invalid(f"unsupported declared_mime {source.declared_mime!r}")

        span.set_attribute("ocr.caller_id", request.caller_id)
        span.set_attribute("ocr.schema_id", request.schema_id)


def _trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _fill_result(target, result: dict) -> None:
    target.schema_version = result.get("schema_version", "1.0")
    target.avg_confidence = float(result.get("avg_confidence", 0.0))
    for item in result.get("fields", []):
        field = target.fields.add()
        field.name = item.get("name", "")
        field.status = FIELD_STATUS_MAP.get(
            item.get("status", "missing"), document_pb2.FIELD_STATUS_UNSPECIFIED
        )
        field.confidence = float(item.get("confidence", 0.0))
        if item.get("value"):
            field.value = str(item["value"])
        if item.get("amount") is not None:
            field.amount = int(item["amount"])
        if item.get("currency"):
            field.currency = item["currency"]
        bbox = item.get("bbox")
        if bbox:
            field.bbox.x1 = int(bbox.get("x1", 0))
            field.bbox.y1 = int(bbox.get("y1", 0))
            field.bbox.x2 = int(bbox.get("x2", 0))
            field.bbox.y2 = int(bbox.get("y2", 0))
        field.page = int(item.get("page", 1))
    for issue in result.get("issues", []):
        entry = target.issues.add()
        entry.code = issue.get("code", "")
        entry.field = issue.get("field", "")
        entry.detail = issue.get("detail", "")
