"""Anchor Zammad ticket to first-seen customer (APPENG-4759).

Revision ID: 004
Revises: 003
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zammad_ticket_customer_anchors",
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("zammad_customer_id", sa.BigInteger(), nullable=True),
        sa.Column("email_normalized", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ticket_id"),
    )


def downgrade() -> None:
    op.drop_table("zammad_ticket_customer_anchors")
