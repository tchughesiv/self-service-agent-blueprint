"""Add integration_default_configs table

Revision ID: 002
Revises: 001
Create Date: 2025-09-18 12:29:16.194447

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Create integration_default_configs table using raw SQL
    # The integrationtype enum already exists from migration 001
    connection = op.get_bind()

    # Create the table using raw SQL to avoid ENUM creation issues
    connection.execute(
        sa.text(
            """
        CREATE TABLE integration_default_configs (
            id SERIAL PRIMARY KEY,
            integration_type integrationtype NOT NULL,
            enabled BOOLEAN NOT NULL,
            config JSONB NOT NULL,
            priority INTEGER NOT NULL,
            retry_count INTEGER NOT NULL,
            retry_delay_seconds INTEGER NOT NULL,
            created_by VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_integration_default_type UNIQUE (integration_type)
        )
    """
        )
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_table("integration_default_configs")
