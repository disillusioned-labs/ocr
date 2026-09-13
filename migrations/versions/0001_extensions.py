"""extensions

Revision ID: 0001
Revises:
Create Date: 2026-09-13
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres 18 ships uuidv7() natively; pgcrypto covers older instances.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    pass
