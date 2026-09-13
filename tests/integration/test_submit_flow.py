"""Live-Postgres contract checks (M1 acceptance): idempotency, status flow, outbox row.

Run with: OCR_DATABASE_DSN=postgres://... uv run pytest -m integration
Skipped automatically when the database is unreachable.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

DSN = os.environ.get("OCR_DATABASE_DSN", "postgres://ocr_app:devpassword@localhost:5432/ocr")


@pytest.fixture()
async def store():
    from ocr_engine.repo.store import Store, create_pool

    try:
        pool = await create_pool(DSN, min_size=1, max_size=2, timeout=3)
    except Exception:
        pytest.skip(f"Postgres not reachable at {DSN}")
    try:
        yield Store(pool=pool)
    finally:
        await pool.close()


async def test_double_submit_yields_one_row_and_existing_id(store):
    key = str(uuid.uuid4())
    first, second = uuid.uuid4(), uuid.uuid4()
    common = dict(
        idempotency_key=key,
        external_ref="ext-1",
        schema_id="receipt@1",
        caller_id="expense",
        source_bucket="expense-files",
        source_path=f"receipts/{key}.jpg",
        declared_mime="image/jpeg",
        size_bytes=1024,
        trace_id=None,
    )

    assert await store.submit_document(document_id=first, **common) is True
    assert await store.submit_document(document_id=second, **common) is False

    existing = await store.get_idempotent_document(key)
    assert existing is not None and existing.id == first
    assert existing.status == "queued"


async def test_finish_document_writes_result_and_outbox_atomically(store):
    key = str(uuid.uuid4())
    doc_id = uuid.uuid4()
    await store.submit_document(
        document_id=doc_id,
        idempotency_key=key,
        external_ref="ext-2",
        schema_id="receipt@1",
        caller_id="expense",
        source_bucket="expense-files",
        source_path=f"receipts/{key}.jpg",
        declared_mime="image/jpeg",
        size_bytes=1024,
        trace_id=None,
    )
    await store.mark_processing(doc_id)

    from ocr_engine.events.envelope import build_envelope

    doc = await store.get_document(doc_id)
    result = {
        "schema_version": "1.0",
        "avg_confidence": 0.97,
        "fields": [
            {"name": "total", "amount": 27800, "currency": "IDR", "confidence": 0.94, "status": "extracted"}
        ],
        "issues": [],
    }
    payload = build_envelope(doc, status="completed", result=result, error=None)
    await store.finish_document(
        doc_id,
        status="completed",
        result=result,
        avg_confidence=0.97,
        error_code=None,
        error_message=None,
        payload=payload,
        topic="ocr.document.processed.v1",
        trace_id=None,
    )

    finished = await store.get_document(doc_id)
    assert finished.status == "completed"
    assert finished.result["fields"][0]["amount"] == 27800

    outbox = await store.pool.fetchrow(
        "SELECT event_type, topic, payload FROM outbox_events WHERE aggregate_id = $1", doc_id
    )
    assert outbox is not None
    assert outbox["event_type"] == "document.processed"
    assert outbox["payload"]["data"]["fields"][0]["amount"] == 27800
    assert outbox["payload"]["error"] is None
    assert outbox["payload"]["caller_id"] == "expense"


async def test_failed_document_carries_error_envelope(store):
    key = str(uuid.uuid4())
    doc_id = uuid.uuid4()
    await store.submit_document(
        document_id=doc_id,
        idempotency_key=key,
        external_ref="ext-3",
        schema_id="receipt@1",
        caller_id="expense",
        source_bucket="expense-files",
        source_path=f"receipts/{key}.jpg",
        declared_mime="image/jpeg",
        size_bytes=1024,
        trace_id=None,
    )
    from ocr_engine.events.envelope import build_envelope

    doc = await store.get_document(doc_id)
    await store.finish_document(
        doc_id,
        status="failed",
        result=None,
        avg_confidence=None,
        error_code="STORAGE_OBJECT_MISSING",
        error_message="bucket/object",
        payload=build_envelope(
            doc, status="failed", result=None,
            error={"code": "STORAGE_OBJECT_MISSING", "message": "bucket/object"},
        ),
        topic="ocr.document.processed.v1",
        trace_id=None,
    )

    failed = await store.get_document(doc_id)
    assert failed.status == "failed"
    assert failed.error_code == "STORAGE_OBJECT_MISSING"
    outbox = await store.pool.fetchrow(
        "SELECT payload FROM outbox_events WHERE aggregate_id = $1", doc_id
    )
    assert outbox["payload"]["data"] is None
    assert outbox["payload"]["error"]["code"] == "STORAGE_OBJECT_MISSING"
