"""Add request status, processing_started_at, pod_heartbeats, and DB timestamping.

Adds:
- status column to request_logs (pending | processing | completed | failed)
- processing_started_at column to request_logs
- Index (session_id, status, created_at) for dequeue and reclaim queries
- pod_heartbeats table for pod liveness (reclaim)
- server_default for request_logs.created_at, request_logs.updated_at
- server_default for request_sessions.created_at, processed_events.created_at
- trigger to set request_logs.updated_at on UPDATE (DB clock for multi-pod ordering)

Backfill: Existing request_logs rows receive status='completed' via server_default.
They are not reclaimed or dequeued. New requests use the full status lifecycle.

pod_heartbeats: Each request-manager pod updates its row periodically. Reclaim
uses stale last_check_in_at to detect dead pods and reset stuck processing rows.

Revision ID: 004
Revises: 003
Create Date: 2025-01-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Add status column to request_logs (default 'completed' for backfill)
    op.add_column(
        "request_logs",
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="completed",
        ),
    )

    # Add processing_started_at column (nullable - set when status becomes 'processing')
    op.add_column(
        "request_logs",
        sa.Column(
            "processing_started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )

    # Create index for dequeue and reclaim: (session_id, status, created_at)
    op.create_index(
        "ix_request_logs_session_status_created",
        "request_logs",
        ["session_id", "status", "created_at"],
        unique=False,
    )

    # Partial index for reclaim_stuck_processing_global (status='processing' only)
    op.create_index(
        "ix_request_logs_status_processing_started",
        "request_logs",
        ["status", "processing_started_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )

    # Create pod_heartbeats table
    op.create_table(
        "pod_heartbeats",
        sa.Column("pod_name", sa.String(length=255), nullable=False),
        sa.Column(
            "last_check_in_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("pod_name", name=op.f("pk_pod_heartbeats")),
    )

    # DB timestamping for multi-pod ordering (avoids clock skew across replicas)
    op.alter_column(
        "request_logs",
        "created_at",
        server_default=sa.text("CURRENT_TIMESTAMP"),
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "request_logs",
        "updated_at",
        server_default=sa.text("CURRENT_TIMESTAMP"),
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "request_sessions",
        "created_at",
        server_default=sa.text("CURRENT_TIMESTAMP"),
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "processed_events",
        "created_at",
        server_default=sa.text("CURRENT_TIMESTAMP"),
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )

    # Trigger: set request_logs.updated_at on UPDATE (SQLAlchemy onupdate is pod clock)
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION set_request_logs_updated_at()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_request_logs_updated_at
            BEFORE UPDATE ON request_logs
            FOR EACH ROW EXECUTE PROCEDURE set_request_logs_updated_at();
        """
        )
    )


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop trigger and function for request_logs.updated_at
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_request_logs_updated_at ON request_logs")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS set_request_logs_updated_at()"))

    # Remove server_default from timestamp columns
    op.alter_column(
        "processed_events",
        "created_at",
        server_default=None,
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "request_sessions",
        "created_at",
        server_default=None,
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "request_logs",
        "updated_at",
        server_default=None,
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "request_logs",
        "created_at",
        server_default=None,
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
    )

    # Drop pod_heartbeats table
    op.drop_table("pod_heartbeats")

    # Drop indexes
    op.drop_index(
        "ix_request_logs_status_processing_started",
        table_name="request_logs",
    )
    op.drop_index(
        "ix_request_logs_session_status_created",
        table_name="request_logs",
    )

    # Drop columns from request_logs
    op.drop_column("request_logs", "processing_started_at")
    op.drop_column("request_logs", "status")
