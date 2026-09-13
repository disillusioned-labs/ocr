"""outbox_events

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE outbox_events (
            id              UUID         PRIMARY KEY DEFAULT uuidv7(),
            aggregate_type  VARCHAR(50)  NOT NULL,
            aggregate_id    UUID         NOT NULL,
            event_type      VARCHAR(100) NOT NULL,
            event_version   INT          NOT NULL DEFAULT 1,
            topic           VARCHAR(200) NOT NULL,
            payload         JSONB        NOT NULL,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            published_at    TIMESTAMPTZ  NULL,
            trace_id        VARCHAR(255) NULL,
            attempt_count   INT          NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ  NULL,
            locked_at       TIMESTAMPTZ  NULL,
            locked_by       VARCHAR(255) NULL,
            last_error      TEXT         NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_outbox_events_pending ON outbox_events (created_at, id) WHERE published_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_outbox_events_retry ON outbox_events (next_attempt_at, id) "
        "WHERE published_at IS NULL AND next_attempt_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_outbox_events_aggregate ON outbox_events (aggregate_type, aggregate_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outbox_events")
