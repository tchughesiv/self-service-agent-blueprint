"""Fix enum case to match Python enum names

Revision ID: 002
Revises: 001
Create Date: 2025-01-09 00:00:00.000000

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
    """Upgrade database schema - convert enum values to uppercase."""
    connection = op.get_bind()

    # Map of enum type to (old_value, new_value) pairs
    enum_updates = {
        "integrationtype": [
            ("slack", "SLACK"),
            ("web", "WEB"),
            ("cli", "CLI"),
            ("tool", "TOOL"),
            ("email", "EMAIL"),
            ("sms", "SMS"),
            ("webhook", "WEBHOOK"),
            ("teams", "TEAMS"),
            ("discord", "DISCORD"),
        ],
        "sessionstatus": [
            ("active", "ACTIVE"),
            ("inactive", "INACTIVE"),
            ("expired", "EXPIRED"),
            ("archived", "ARCHIVED"),
        ],
        "deliverystatus": [
            ("pending", "PENDING"),
            ("delivered", "DELIVERED"),
            ("failed", "FAILED"),
            ("retrying", "RETRYING"),
            ("expired", "EXPIRED"),
        ],
    }

    # Update enum values
    for enum_name, value_pairs in enum_updates.items():
        for old_value, new_value in value_pairs:
            try:
                connection.execute(
                    sa.text(
                        f"ALTER TYPE {enum_name} RENAME VALUE '{old_value}' TO '{new_value}'"
                    )
                )
            except Exception as e:
                # If the value doesn't exist or is already renamed, continue
                print(
                    f"Warning: Could not rename {enum_name}.{old_value} to {new_value}: {e}"
                )


def downgrade() -> None:
    """Downgrade database schema - convert enum values back to lowercase."""
    connection = op.get_bind()

    # Map of enum type to (new_value, old_value) pairs (reversed)
    enum_updates = {
        "integrationtype": [
            ("SLACK", "slack"),
            ("WEB", "web"),
            ("CLI", "cli"),
            ("TOOL", "tool"),
            ("EMAIL", "email"),
            ("SMS", "sms"),
            ("WEBHOOK", "webhook"),
            ("TEAMS", "teams"),
            ("DISCORD", "discord"),
        ],
        "sessionstatus": [
            ("ACTIVE", "active"),
            ("INACTIVE", "inactive"),
            ("EXPIRED", "expired"),
            ("ARCHIVED", "archived"),
        ],
        "deliverystatus": [
            ("PENDING", "pending"),
            ("DELIVERED", "delivered"),
            ("FAILED", "failed"),
            ("RETRYING", "retrying"),
            ("EXPIRED", "expired"),
        ],
    }

    # Update enum values back
    for enum_name, value_pairs in enum_updates.items():
        for new_value, old_value in value_pairs:
            try:
                connection.execute(
                    sa.text(
                        f"ALTER TYPE {enum_name} RENAME VALUE '{new_value}' TO '{old_value}'"
                    )
                )
            except Exception as e:
                # If the value doesn't exist or is already renamed, continue
                print(
                    f"Warning: Could not rename {enum_name}.{new_value} to {old_value}: {e}"
                )
