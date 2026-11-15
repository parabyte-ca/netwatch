"""v0.21: who's home (people and the devices that are theirs).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("colour", sa.String(16), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_ms", sa.Integer(), nullable=False),
    )
    op.create_table(
        "people_devices",
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_people_devices_device", "people_devices", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_people_devices_device", table_name="people_devices")
    op.drop_table("people_devices")
    op.drop_table("people")
