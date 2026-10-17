"""Monitoring: events timeline, NIC statistics, path tests, iperf3 servers and results.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="SET NULL")),
        sa.Column("iface", sa.String(32)),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer()),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
    )
    op.create_index("ix_events_start", "events", ["start_ms"])
    op.create_index("ix_events_target", "events", ["target_id"])

    op.create_table(
        "nic_stats",
        sa.Column("iface", sa.String(32), primary_key=True),
        sa.Column("bucket_ts", sa.Integer(), primary_key=True),
        sa.Column("operstate", sa.String(16)),
        sa.Column("speed", sa.Integer()),
        sa.Column("duplex", sa.String(8)),
        sa.Column("carrier_changes", sa.Integer(), nullable=False),
        sa.Column("rx_bytes", sa.Integer(), nullable=False),
        sa.Column("tx_bytes", sa.Integer(), nullable=False),
        sa.Column("rx_packets", sa.Integer(), nullable=False),
        sa.Column("tx_packets", sa.Integer(), nullable=False),
        sa.Column("rx_errors", sa.Integer(), nullable=False),
        sa.Column("tx_errors", sa.Integer(), nullable=False),
        sa.Column("rx_dropped", sa.Integer(), nullable=False),
        sa.Column("tx_dropped", sa.Integer(), nullable=False),
        sa.Column("rx_crc_errors", sa.Integer(), nullable=False),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_nic_stats_ts", "nic_stats", ["bucket_ts"])

    op.create_table(
        "path_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts_ms", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="SET NULL")),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="SET NULL")),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("hop_count", sa.Integer()),
        sa.Column("reached", sa.Boolean()),
        sa.Column("first_loss_hop", sa.Integer()),
        sa.Column("path_changed", sa.Boolean()),
        sa.Column("hops", sa.Text(), nullable=False),
        sa.Column("error", sa.String(200)),
    )
    op.create_index("ix_path_tests_ts", "path_tests", ["ts_ms"])
    op.create_index("ix_path_tests_host", "path_tests", ["host"])

    op.create_table(
        "iperf_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("parallel", sa.Integer(), nullable=False),
        sa.Column("interval_min", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )

    op.create_table(
        "throughput_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts_ms", sa.Integer(), nullable=False),
        sa.Column(
            "server_id", sa.Integer(), sa.ForeignKey("iperf_servers.id", ondelete="SET NULL")
        ),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("duration_s", sa.Float()),
        sa.Column("bits_per_second", sa.Float()),
        sa.Column("retransmits", sa.Integer()),
        sa.Column("error", sa.String(200)),
        sa.Column("details", sa.Text(), nullable=False),
    )
    op.create_index("ix_throughput_tests_ts", "throughput_tests", ["ts_ms"])


def downgrade() -> None:
    op.drop_index("ix_throughput_tests_ts", "throughput_tests")
    op.drop_table("throughput_tests")
    op.drop_table("iperf_servers")
    op.drop_index("ix_path_tests_host", "path_tests")
    op.drop_index("ix_path_tests_ts", "path_tests")
    op.drop_table("path_tests")
    op.drop_index("ix_nic_stats_ts", "nic_stats")
    op.drop_table("nic_stats")
    op.drop_index("ix_events_target", "events")
    op.drop_index("ix_events_start", "events")
    op.drop_table("events")
