"""Admin authentication: users and server-side sessions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_ms", sa.Integer(), nullable=False),
        sa.Column("last_seen_ms", sa.Integer(), nullable=False),
        sa.Column("expires_ms", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(200)),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user", "sessions")
    op.drop_table("sessions")
    op.drop_table("users")
