"""v0.15: quiet device groups.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_groups",
        sa.Column("quiet", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("device_groups") as b:
        b.drop_column("quiet")
