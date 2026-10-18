"""v0.9: exposure scans, open services per device, weekly health reports.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_services",
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("port", sa.Integer(), primary_key=True),
        sa.Column("first_seen_ms", sa.Integer(), nullable=False),
        sa.Column("last_seen_ms", sa.Integer(), nullable=False),
        sa.Column("banner", sa.String(120)),
        sqlite_with_rowid=False,
    )
    op.create_table(
        "exposure_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts_ms", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("hosts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_ports", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(200)),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_exposure_scans_ts", "exposure_scans", ["ts_ms"])
    op.create_table(
        "health_reports",
        sa.Column("week_start", sa.String(10), primary_key=True),
        sa.Column("created_ms", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer()),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("health_reports")
    op.drop_index("ix_exposure_scans_ts", "exposure_scans")
    op.drop_table("exposure_scans")
    op.drop_table("device_services")
