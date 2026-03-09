"""Zammad: allow multiple active sessions per user (one per ticket); widen session_id.

Revision ID: 003
Revises: 002
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace one-active-session index with version that excludes ZAMMAD; widen FK columns."""
    op.execute("DROP INDEX IF EXISTS idx_one_active_session_per_user_integration")
    op.execute(
        """
        CREATE UNIQUE INDEX idx_one_active_session_per_user_integration
        ON request_sessions (user_id, integration_type)
        WHERE status = 'ACTIVE'
          AND integration_type IS NOT NULL
          AND integration_type <> 'ZAMMAD'::integrationtype
        """
    )

    op.alter_column(
        "request_sessions",
        "session_id",
        existing_type=sa.String(length=36),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "request_logs",
        "session_id",
        existing_type=sa.String(length=36),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "delivery_logs",
        "session_id",
        existing_type=sa.String(length=36),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Restore original index; narrow columns (may fail if values > 36 chars exist)."""
    op.execute("DROP INDEX IF EXISTS idx_one_active_session_per_user_integration")
    op.execute(
        """
        CREATE UNIQUE INDEX idx_one_active_session_per_user_integration
        ON request_sessions (user_id, integration_type)
        WHERE status = 'ACTIVE'
        """
    )

    op.alter_column(
        "delivery_logs",
        "session_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=36),
        existing_nullable=False,
    )
    op.alter_column(
        "request_logs",
        "session_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=36),
        existing_nullable=False,
    )
    op.alter_column(
        "request_sessions",
        "session_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=36),
        existing_nullable=False,
    )
