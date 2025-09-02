"""Add missing session fields

Revision ID: 003
Revises: 002
Create Date: 2025-01-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing fields to request_sessions table."""
    # Add external_session_id column
    op.add_column(
        "request_sessions",
        sa.Column("external_session_id", sa.String(length=255), nullable=True),
    )

    # Add agent tracking columns
    op.add_column(
        "request_sessions",
        sa.Column("current_agent_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "request_sessions",
        sa.Column("llama_stack_session_id", sa.String(length=255), nullable=True),
    )

    # Add context columns
    op.add_column(
        "request_sessions", sa.Column("user_context", sa.JSON(), nullable=True)
    )
    op.add_column(
        "request_sessions", sa.Column("conversation_context", sa.JSON(), nullable=True)
    )

    # Add statistics column
    op.add_column(
        "request_sessions",
        sa.Column("last_request_id", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    """Remove the added fields from request_sessions table."""
    # Drop in reverse order
    op.drop_column("request_sessions", "last_request_id")
    op.drop_column("request_sessions", "conversation_context")
    op.drop_column("request_sessions", "user_context")
    op.drop_column("request_sessions", "llama_stack_session_id")
    op.drop_column("request_sessions", "current_agent_id")
    op.drop_column("request_sessions", "external_session_id")
