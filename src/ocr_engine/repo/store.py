"""asyncpg data access shared by the api, worker, and outbox processes."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg


def create_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> Any:
    return asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)


@dataclass
class DocumentRow:
    id: uuid.UUID
    idempotency_key: str
    external_ref: str
    doc_type: str
    status: str
    caller_id: str
    source_bucket: str
    source_path: str
    declared_mime: str
    size_bytes: int
    result: dict | None
    avg_confidence: float | None
    error_code: str | None
    error_message: str | None
    trace_id: str | None


_ROW_SQL = """
SELECT id, idempotency_key, external_ref, doc_type, status, caller_id,
       source_bucket, source_path, declared_mime, size_bytes,
       result, avg_confidence, error_code, error_message, trace_id
FROM documents
"""


def _row_to_document(row: asyncpg.Record) -> DocumentRow:
    return DocumentRow(
        id=row["id"],
        idempotency_key=row["idempotency_key"],
        external_ref=row["external_ref"],
        doc_type=row["doc_type"],
        status=row["status"],
        caller_id=row["caller_id"],
        source_bucket=row["source_bucket"],
        source_path=row["source_path"],
        declared_mime=row["declared_mime"],
        size_bytes=row["size_bytes"],
        result=json.loads(row["result"]) if row["result"] else None,
        avg_confidence=row["avg_confidence"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        trace_id=row["trace_id"],
    )


@dataclass
class Store:
    pool: asyncpg.Pool

    async def close(self) -> None:
        await self.pool.close()

    async def submit_document(
        self,
        *,
        document_id: uuid.UUID,
        idempotency_key: str,
        external_ref: str,
        schema_id: str,
        caller_id: str,
        source_bucket: str,
        source_path: str,
        declared_mime: str,
        size_bytes: int,
        trace_id: str | None,
    ) -> bool:
        """Insert a queued document. False means the idempotency key already exists."""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(
                    """INSERT INTO documents
                           (id, idempotency_key, external_ref, doc_type, status, caller_id,
                            source_bucket, source_path, declared_mime, size_bytes, trace_id)
                       VALUES ($1, $2, $3, $4, 'queued', $5, $6, $7, $8, $9, $10)""",
                    document_id,
                    idempotency_key,
                    external_ref,
                    schema_id,
                    caller_id,
                    source_bucket,
                    source_path,
                    declared_mime,
                    size_bytes,
                    trace_id,
                )
                return True
            except asyncpg.UniqueViolationError:
                return False

    async def get_idempotent_document(self, idempotency_key: str) -> DocumentRow | None:
        row = await self.pool.fetchrow(_ROW_SQL + "WHERE idempotency_key = $1", idempotency_key)
        return _row_to_document(row) if row else None

    async def get_document(self, document_id: uuid.UUID) -> DocumentRow | None:
        row = await self.pool.fetchrow(_ROW_SQL + "WHERE id = $1", document_id)
        return _row_to_document(row) if row else None

    async def mark_processing(self, document_id: uuid.UUID) -> None:
        await self.pool.execute(
            "UPDATE documents SET status = 'processing', updated_at = now() "
            "WHERE id = $1 AND status = 'queued'",
            document_id,
        )

    async def finish_document(
        self,
        document_id: uuid.UUID,
        *,
        status: str,
        result: dict | None,
        avg_confidence: float | None,
        error_code: str | None,
        error_message: str | None,
        payload: dict,
        topic: str,
        trace_id: str | None,
    ) -> None:
        """One transaction: the terminal document update plus its outbox row.

        payload is the complete event envelope built by events.envelope - the
        echo fields (external_ref/caller_id/idempotency_key) come from the
        submit row, never from the worker's inputs.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """UPDATE documents
                       SET status = $2, result = $3, avg_confidence = $4,
                           error_code = $5, error_message = $6,
                           processed_at = now(), updated_at = now()
                       WHERE id = $1""",
                    document_id,
                    status,
                    json.dumps(result) if result is not None else None,
                    avg_confidence,
                    error_code,
                    error_message,
                )
                await conn.execute(
                    """INSERT INTO outbox_events
                           (aggregate_type, aggregate_id, event_type, event_version, topic,
                            payload, trace_id)
                       VALUES ('document', $1, 'document.processed', 1, $2, $3, $4)""",
                    document_id,
                    topic,
                    json.dumps(payload),
                    trace_id,
                )

    async def list_expired_documents(self, older_than) -> list[uuid.UUID]:
        rows = await self.pool.fetch(
            """SELECT id, result, error_message FROM documents
               WHERE processed_at IS NOT NULL AND processed_at < $1
               ORDER BY processed_at LIMIT 100""",
            older_than,
        )
        return [r["id"] for r in rows]

    async def purge_document_results(self, document_ids: list[uuid.UUID]) -> None:
        """Retention: drop result payloads past the retention window (the row stays for the audit trail)."""
        await self.pool.execute(
            """UPDATE documents
               SET result = NULL, error_message = NULL, updated_at = now()
               WHERE id = ANY($1::uuid[])""",
            document_ids,
        )

    async def claim_pending_outbox(self, worker_id: str, batch: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """UPDATE outbox_events
                       SET locked_by = $1, locked_at = now(),
                           attempt_count = attempt_count + 1
                       WHERE id IN (
                           SELECT id FROM outbox_events
                           WHERE published_at IS NULL
                             AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                             AND (locked_at IS NULL OR locked_at < now() - interval '1 minute')
                           ORDER BY created_at
                           LIMIT $2
                           FOR UPDATE SKIP LOCKED
                       )
                       RETURNING *""",
                    worker_id,
                    batch,
                )
        return rows

    async def mark_published(self, event_id: uuid.UUID) -> None:
        await self.pool.execute(
            "UPDATE outbox_events SET published_at = now(), locked_by = NULL, locked_at = NULL WHERE id = $1",
            event_id,
        )

    async def defer_outbox_event(self, event_id: uuid.UUID, error: str) -> None:
        await self.pool.execute(
            """UPDATE outbox_events
               SET locked_by = NULL, locked_at = NULL, last_error = $2,
                   next_attempt_at = now() + make_interval(secs => 5 * LEAST(attempt_count + 1, 6))
               WHERE id = $1""",
            event_id,
            error,
        )

    async def reclaim_abandoned_outbox(self, older_than) -> int:
        result = await self.pool.execute(
            """UPDATE outbox_events
               SET locked_by = NULL, locked_at = NULL
               WHERE published_at IS NULL AND locked_at IS NOT NULL AND locked_at < $1""",
            older_than,
        )
        return int(result.split()[-1])
