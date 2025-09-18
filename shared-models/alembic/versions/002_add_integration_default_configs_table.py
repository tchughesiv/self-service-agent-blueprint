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
    # Create integration_default_configs table
    op.create_table(
        "integration_default_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "integration_type",
            sa.Enum(
                "SLACK",
                "WEB",
                "CLI",
                "TOOL",
                "EMAIL",
                "SMS",
                "WEBHOOK",
                "TEAMS",
                "DISCORD",
                "TEST",
                name="integrationtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_type", name="uq_integration_default_type"),
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_table("integration_default_configs")
