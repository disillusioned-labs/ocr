"""Envelope contract: echo fields, data/error exclusivity."""

from __future__ import annotations

import datetime as dt
import json
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
        created_at=dt.datetime.now(dt.UTC),
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
    assert "error" not in env


def test_failed_envelope() -> None:
    env = build_envelope(
        make_doc(),
        status="failed",
        result=None,
        error={"code": "FILE_CORRUPT", "message": "bad bytes"},
    )
    assert "data" not in env
    assert env["error"] == {"code": "FILE_CORRUPT", "message": "bad bytes"}


def test_data_and_error_never_coexist() -> None:
    doc = make_doc()
    completed = build_envelope(doc, status="completed", result={}, error={"code": "X", "message": ""})
    failed = build_envelope(doc, status="failed", result={}, error=None)
    assert completed["data"] == {} and "error" not in completed
    assert "data" not in failed and "error" not in failed


def test_serialized_envelope_decodes_to_exactly_one_of_data_or_error() -> None:
    # The wire invariant the Go consumer enforces: after JSON decode, exactly
    # one of data/error is non-nil. A serialized null would decode to nil on
    # both sides and dead-letter the event.
    doc = make_doc()
    completed = build_envelope(doc, status="completed", result={"fields": []}, error=None)
    failed = build_envelope(doc, status="failed", result=None, error={"code": "INTERNAL", "message": "x"})
    completed, failed = json.loads(json.dumps(completed)), json.loads(json.dumps(failed))
    assert (completed["data"] is not None) != (completed.get("error") is not None)
    assert (failed.get("data") is not None) != (failed["error"] is not None)
