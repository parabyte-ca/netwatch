"""v0.14: device groups and tags.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("colour", sa.String(16), nullable=False),
        sa.Column("icon", sa.String(16), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_ms", sa.Integer(), nullable=False),
    )
    # A plain column: adding a foreign key would mean rebuilding devices, which other tables
    # cascade from. GroupStore.delete ungroups the devices itself.
    op.add_column("devices", sa.Column("group_id", sa.Integer()))
    op.create_index("ix_devices_group", "devices", ["group_id"])
    op.create_table(
        "device_tags",
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag", sa.String(24), primary_key=True),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_device_tags_tag", "device_tags", ["tag"])


def downgrade() -> None:
    op.drop_index("ix_device_tags_tag", table_name="device_tags")
    op.drop_table("device_tags")
    op.drop_index("ix_devices_group", table_name="devices")
    with op.batch_alter_table("devices") as b:
        b.drop_column("group_id")
    op.drop_table("device_groups")
