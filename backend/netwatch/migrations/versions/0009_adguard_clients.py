"""v0.12: AdGuard Home queries per client and hour.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adguard_clients",
        sa.Column("hour_ms", sa.Integer(), primary_key=True),
        sa.Column("client", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("queries", sa.Integer(), nullable=False),
        sa.Column("blocked", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("domains", sa.Text()),
        sa.Column("blocked_domains", sa.Text()),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_adguard_clients_device", "adguard_clients", ["device_id", "hour_ms"])


def downgrade() -> None:
    op.drop_index("ix_adguard_clients_device", table_name="adguard_clients")
    op.drop_table("adguard_clients")
