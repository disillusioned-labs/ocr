"""Event envelope per api-ocr.md: data and error are mutually exclusive."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ..repo.store import DocumentRow

SCHEMA_VERSION = "1.0"
EVENT_TYPE = "document.processed"


def build_envelope(
    doc: DocumentRow,
    *,
    status: str,
    result: dict | None,
    error: dict | None,
) -> dict:
    # data and error are omitted, never null: consumers (expense) treat
    # "both absent after decode" as a contract violation and dead-letter.
    envelope: dict = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "event_type": EVENT_TYPE,
        "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "document_id": str(doc.id),
        "external_ref": doc.external_ref,
        "caller_id": doc.caller_id,
        "idempotency_key": doc.idempotency_key,
        "producer": "ocr",
    }
    if status in ("completed", "needs_review"):
        envelope["data"] = result
    if status == "failed" and error is not None:
        envelope["error"] = error
    return envelope
