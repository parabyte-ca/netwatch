"""v0.11: AdGuard Home readings.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adguard_stats",
        sa.Column("ts_ms", sa.Integer(), primary_key=True),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("protection", sa.Boolean()),
        sa.Column("queries", sa.Integer()),
        sa.Column("blocked", sa.Integer()),
        sa.Column("servfail", sa.Integer()),
        sa.Column("errors", sa.Integer()),
        sa.Column("avg_ms", sa.Float()),
        sa.Column("p95_ms", sa.Float()),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("upstreams", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
        sqlite_with_rowid=False,
    )


def downgrade() -> None:
    op.drop_table("adguard_stats")
