"""v0.26: saved servers (name, address, port, user) for the SSH app link.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("notes", sa.String(200), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_ms", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("servers")
