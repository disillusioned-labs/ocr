"""documents

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE documents (
            id              UUID         PRIMARY KEY DEFAULT uuidv7(),
            idempotency_key UUID         NOT NULL,
            external_ref    TEXT         NOT NULL,
            doc_type        VARCHAR(50)  NOT NULL,
            status          VARCHAR(20)  NOT NULL DEFAULT 'queued',
            caller_id       TEXT         NOT NULL,
            source_bucket   TEXT         NOT NULL,
            source_path     TEXT         NOT NULL,
            declared_mime   VARCHAR(100) NOT NULL,
            size_bytes      BIGINT       NOT NULL,

            result          JSONB        NULL,
            avg_confidence  DOUBLE PRECISION NULL,
            error_code      VARCHAR(50)  NULL,
            error_message   TEXT         NULL,
            trace_id        TEXT         NULL,

            processed_at    TIMESTAMPTZ  NULL,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT ck_documents_status
                CHECK (status IN ('queued', 'processing', 'completed', 'needs_review', 'failed'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_documents_idempotency_key ON documents (idempotency_key)")
    op.execute("CREATE INDEX ix_documents_status ON documents (status, created_at)")
    op.execute("CREATE INDEX ix_documents_external_ref ON documents (external_ref)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents")
