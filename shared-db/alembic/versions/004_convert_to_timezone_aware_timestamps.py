"""Convert datetime columns to timezone-aware timestamps

Revision ID: 004
Revises: 003
Create Date: 2025-01-09 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert datetime columns to TIMESTAMP WITH TIME ZONE."""

    # Tables and their datetime columns to convert
    tables_columns = {
        "request_sessions": [
            "last_request_at",
            "expires_at",
            "created_at",
            "updated_at",
        ],
        "request_logs": ["completed_at", "created_at", "updated_at"],
        "user_integration_configs": ["created_at", "updated_at"],
        "integration_templates": ["created_at", "updated_at"],
        "delivery_logs": [
            "first_attempt_at",
            "last_attempt_at",
            "delivered_at",
            "expires_at",
            "created_at",
            "updated_at",
        ],
        "integration_credentials": ["created_at", "updated_at"],
    }

    # Convert each column to timezone-aware
    for table_name, columns in tables_columns.items():
        for column_name in columns:
            # Use ALTER COLUMN to change the type
            op.execute(
                f"""
                ALTER TABLE {table_name}
                ALTER COLUMN {column_name}
                TYPE TIMESTAMP WITH TIME ZONE
                USING {column_name} AT TIME ZONE 'UTC'
            """
            )


def downgrade() -> None:
    """Convert datetime columns back to TIMESTAMP WITHOUT TIME ZONE."""

    # Tables and their datetime columns to convert back
    tables_columns = {
        "request_sessions": [
            "last_request_at",
            "expires_at",
            "created_at",
            "updated_at",
        ],
        "request_logs": ["completed_at", "created_at", "updated_at"],
        "user_integration_configs": ["created_at", "updated_at"],
        "integration_templates": ["created_at", "updated_at"],
        "delivery_logs": [
            "first_attempt_at",
            "last_attempt_at",
            "delivered_at",
            "expires_at",
            "created_at",
            "updated_at",
        ],
        "integration_credentials": ["created_at", "updated_at"],
    }

    # Convert each column back to timezone-naive
    for table_name, columns in tables_columns.items():
        for column_name in columns:
            # Use ALTER COLUMN to change the type back
            op.execute(
                f"""
                ALTER TABLE {table_name}
                ALTER COLUMN {column_name}
                TYPE TIMESTAMP WITHOUT TIME ZONE
                USING {column_name} AT TIME ZONE 'UTC'
            """
            )
