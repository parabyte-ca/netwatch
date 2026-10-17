"""Initial schema: targets, samples, 1-minute rollups, key/value store.

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer()),
        sa.Column("url", sa.String(2048)),
        sa.Column("dns_query", sa.String(253)),
        sa.Column("dns_rdtype", sa.String(8)),
        sa.Column("expect_status", sa.Integer()),
        sa.Column("interval_s", sa.Float(), nullable=False),
        sa.Column("timeout_s", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("group", sa.String(16), nullable=False),
        sa.Column("auto", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "samples",
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ts_ms", sa.Integer(), primary_key=True),
        sa.Column("rtt_ms", sa.Float()),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(64)),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_samples_ts", "samples", ["ts_ms"])
    op.create_table(
        "rollup_1m",
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bucket_ts", sa.Integer(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("lost", sa.Integer(), nullable=False),
        sa.Column("min", sa.Float()),
        sa.Column("p5", sa.Float()),
        sa.Column("p25", sa.Float()),
        sa.Column("median", sa.Float()),
        sa.Column("p75", sa.Float()),
        sa.Column("p95", sa.Float()),
        sa.Column("max", sa.Float()),
        sa.Column("avg", sa.Float()),
        sa.Column("jitter", sa.Float()),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_rollup_1m_ts", "rollup_1m", ["bucket_ts"])
    op.create_table(
        "kv",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("kv")
    op.drop_index("ix_rollup_1m_ts", "rollup_1m")
    op.drop_table("rollup_1m")
    op.drop_index("ix_samples_ts", "samples")
    op.drop_table("samples")
    op.drop_table("targets")
