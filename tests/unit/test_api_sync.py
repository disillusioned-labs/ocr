"""ProcessDocument (sync RPC): cap, inline run, idempotent replay, cancellation."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from types import SimpleNamespace

import grpc
import pytest

from ocr_engine.api.servicer import DocumentServiceServicer
from ocr_engine.repo.store import DocumentRow
from ocr_engine.settings import Settings


class _Abort(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


class _FakeContext:
    async def abort(self, code, message):
        raise _Abort(code, message)


class _FakeStore:
    def __init__(self, row: DocumentRow):
        self.row = row
        self.inserted = True
        self.finished = None

    async def submit_document(self, **kwargs):
        return self.inserted

    async def get_idempotent_document(self, idempotency_key):
        return self.row if not self.inserted else None

    async def get_document(self, document_id):
        return self.row

    async def finish_document(self, document_id, **kwargs):
        self.finished = kwargs


def _settings() -> Settings:
    import pathlib

    kwargs = {
        "database_dsn": "postgres://ocr_app:pw@localhost:5432/ocr",
        "kafka_brokers": "localhost:9092",
        "allowed_buckets": ["expense-files"],
        "schema_dir": pathlib.Path(__file__).resolve().parents[2] / "schemas",
        "baidu_api_url": "https://x.aistudio-app.com/layout-parsing",
        "baidu_token": "test-token-value",
    }
    return Settings(**kwargs)


def _row(status="completed", result=None) -> DocumentRow:
    return DocumentRow(
        id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        external_ref="doc-123",
        doc_type="receipt@1",
        status=status,
        caller_id="expense",
        source_bucket="expense-files",
        source_path="receipts/abc.jpg",
        declared_mime="image/jpeg",
        size_bytes=1024,
        result=result,
        avg_confidence=None,
        error_code=None,
        error_message=None,
        trace_id=None,
        created_at=dt.datetime.now(dt.UTC),
    )


def _request(size=1024) -> SimpleNamespace:
    return SimpleNamespace(
        idempotency_key=str(uuid.uuid4()),
        external_ref="doc-123",
        schema_id="receipt@1",
        caller_id="expense",
        source=SimpleNamespace(
            bucket="expense-files",
            storage_path="receipts/abc.jpg",
            size_bytes=size,
            declared_mime="image/jpeg",
        ),
    )


def _servicer(store: _FakeStore) -> DocumentServiceServicer:
    return DocumentServiceServicer(store, _settings(), {"receipt@1": object()}, pipeline_ctx={})


@pytest.fixture()
def _no_pipeline(monkeypatch):
    async def fake_process(ctx, document_id):
        return None

    monkeypatch.setattr("ocr_engine.api.servicer.process_document", fake_process)


@pytest.mark.asyncio
async def test_sync_size_cap_rejected_before_any_write(_no_pipeline):
    store = _FakeStore(_row())
    servicer = _servicer(store)
    request = _request(size=servicer.settings.sync_max_bytes + 1)

    with pytest.raises(_Abort) as excinfo:
        await servicer.ProcessDocument(request, _FakeContext())

    assert excinfo.value.code == grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.asyncio
async def test_sync_happy_path_returns_terminal_result(_no_pipeline):
    result = {"schema_version": "1.0", "avg_confidence": 0.9, "fields": [], "issues": []}
    row = _row(status="completed", result=result)
    servicer = _servicer(_FakeStore(row))

    response = await servicer.ProcessDocument(_request(), _FakeContext())

    assert response.status == 3  # DOCUMENT_STATUS_COMPLETED
    assert response.result.avg_confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_sync_duplicate_replays_current_state_without_processing(_no_pipeline, monkeypatch):
    row = _row(status="processing")
    store = _FakeStore(row)
    store.inserted = False
    called = []

    async def spy(ctx, document_id):
        called.append(document_id)

    monkeypatch.setattr("ocr_engine.api.servicer.process_document", spy)
    servicer = _servicer(store)

    response = await servicer.ProcessDocument(_request(), _FakeContext())

    assert response.status == 2  # DOCUMENT_STATUS_PROCESSING
    assert called == []


@pytest.mark.asyncio
async def test_sync_cancellation_marks_document_failed():
    store = _FakeStore(_row(status="processing"))

    async def cancelled(ctx, document_id):
        raise asyncio.CancelledError()

    servicer = _servicer(store)
    servicer._pipeline_ctx = {"k": "v"}
    import ocr_engine.api.servicer as servicer_module

    servicer_module.process_document = cancelled

    with pytest.raises(asyncio.CancelledError):
        await servicer.ProcessDocument(_request(), _FakeContext())

    assert store.finished is not None
    assert store.finished["status"] == "failed"
    assert store.finished["error_code"] == "INTERNAL"
