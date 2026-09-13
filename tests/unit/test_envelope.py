"""Envelope contract: echo fields, data/error exclusivity."""

from __future__ import annotations

import uuid

from ocr_engine.events.envelope import build_envelope
from ocr_engine.repo.store import DocumentRow


def make_doc() -> DocumentRow:
    return DocumentRow(
        id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        external_ref="doc-123",
        doc_type="receipt@1",
        status="queued",
        caller_id="expense",
        source_bucket="expense-files",
        source_path="receipts/abc.jpg",
        declared_mime="image/jpeg",
        size_bytes=1024,
        result=None,
        avg_confidence=None,
        error_code=None,
        error_message=None,
        trace_id=None,
    )


def test_completed_envelope() -> None:
    doc = make_doc()
    env = build_envelope(doc, status="completed", result={"fields": []}, error=None)

    assert env["event_type"] == "document.processed"
    assert env["producer"] == "ocr"
    assert env["document_id"] == str(doc.id)
    assert env["external_ref"] == "doc-123"
    assert env["caller_id"] == "expense"
    assert env["idempotency_key"] == doc.idempotency_key
    assert env["data"] == {"fields": []}
    assert env["error"] is None


def test_failed_envelope() -> None:
    env = build_envelope(
        make_doc(),
        status="failed",
        result=None,
        error={"code": "FILE_CORRUPT", "message": "bad bytes"},
    )
    assert env["data"] is None
    assert env["error"] == {"code": "FILE_CORRUPT", "message": "bad bytes"}


def test_data_and_error_never_coexist() -> None:
    doc = make_doc()
    completed = build_envelope(doc, status="completed", result={}, error={"code": "X", "message": ""})
    failed = build_envelope(doc, status="failed", result={}, error=None)
    assert completed["data"] == {} and completed["error"] is None
    assert failed["data"] is None and failed["error"] is None
