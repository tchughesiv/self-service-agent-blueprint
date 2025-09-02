"""Add TEST integration type

Revision ID: 005
Revises: 004
Create Date: 2025-09-11 06:40:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add TEST integration type to existing enum."""

    # Add TEST to the integration_type enum
    # This approach works with PostgreSQL by adding the new value to the existing enum
    op.execute("ALTER TYPE integrationtype ADD VALUE 'TEST'")


def downgrade() -> None:
    """Remove TEST integration type from enum.

    Note: PostgreSQL doesn't support removing enum values directly.
    This would require recreating the enum and updating all references.
    For now, we'll leave the enum value in place during downgrade.
    """
    # PostgreSQL doesn't support removing enum values easily
    # In a production environment, you would need to:
    # 1. Create a new enum without TEST
    # 2. Update all columns to use the new enum
    # 3. Drop the old enum
    # 4. Rename the new enum

    # For development/testing purposes, we'll leave the enum value
    pass
