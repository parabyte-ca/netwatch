"""Discovery: device inventory and IP history.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mac", sa.String(17), nullable=False, unique=True),
        sa.Column("vendor", sa.String(64)),
        sa.Column("randomised", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(64)),
        sa.Column("hostname", sa.String(64)),
        sa.Column("mdns_name", sa.String(64)),
        sa.Column("model", sa.String(64)),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("type_reason", sa.String(120)),
        sa.Column("type_override", sa.String(16)),
        sa.Column("merged_into", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="SET NULL")),
        sa.Column("first_seen_ms", sa.Integer(), nullable=False),
        sa.Column("last_seen_ms", sa.Integer(), nullable=False),
        sa.Column("last_ip", sa.String(15)),
        sa.Column("notes", sa.String(500)),
        sa.Column("details", sa.Text(), nullable=False),
    )
    op.create_index("ix_devices_last_seen", "devices", ["last_seen_ms"])

    op.create_table(
        "device_ips",
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ip", sa.String(15), primary_key=True),
        sa.Column("first_seen_ms", sa.Integer(), nullable=False),
        sa.Column("last_seen_ms", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_device_ips_ip", "device_ips", ["ip"])


def downgrade() -> None:
    op.drop_index("ix_device_ips_ip", "device_ips")
    op.drop_table("device_ips")
    op.drop_index("ix_devices_last_seen", "devices")
    op.drop_table("devices")
