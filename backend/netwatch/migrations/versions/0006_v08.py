"""v0.8: per-device traffic, bufferbloat tests, Home Hub readings.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_traffic",
        sa.Column("mac", sa.String(17), primary_key=True),
        sa.Column("bucket_ts", sa.Integer(), primary_key=True),
        sa.Column("down_bps", sa.Float(), nullable=False),
        sa.Column("up_bps", sa.Float(), nullable=False),
        sa.Column("down_peak", sa.Float(), nullable=False),
        sa.Column("up_peak", sa.Float(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sqlite_with_rowid=False,
    )
    op.create_table(
        "device_traffic_1h",
        sa.Column("mac", sa.String(17), primary_key=True),
        sa.Column("bucket_ts", sa.Integer(), primary_key=True),
        sa.Column("down_bytes", sa.Float(), nullable=False),
        sa.Column("up_bytes", sa.Float(), nullable=False),
        sa.Column("down_peak", sa.Float(), nullable=False),
        sa.Column("up_peak", sa.Float(), nullable=False),
        sqlite_with_rowid=False,
    )
    op.create_table(
        "bufferbloat_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts_ms", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("grade", sa.String(2)),
        sa.Column("idle_ms", sa.Float()),
        sa.Column("down_ms", sa.Float()),
        sa.Column("up_ms", sa.Float()),
        sa.Column("down_bps", sa.Float()),
        sa.Column("up_bps", sa.Float()),
        sa.Column("error", sa.String(200)),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_bufferbloat_ts", "bufferbloat_tests", ["ts_ms"])
    op.create_table(
        "hub_stats",
        sa.Column("ts_ms", sa.Integer(), primary_key=True),
        sa.Column("uptime_s", sa.Integer()),
        sa.Column("wan_up", sa.Boolean()),
        sa.Column("rx_dbm", sa.Float()),
        sa.Column("tx_dbm", sa.Float()),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
        sqlite_with_rowid=False,
    )


def downgrade() -> None:
    op.drop_table("hub_stats")
    op.drop_index("ix_bufferbloat_ts", table_name="bufferbloat_tests")
    op.drop_table("bufferbloat_tests")
    op.drop_table("device_traffic_1h")
    op.drop_table("device_traffic")
