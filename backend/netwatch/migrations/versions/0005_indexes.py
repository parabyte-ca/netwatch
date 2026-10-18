"""Indexes for the queries the app actually runs.

Drops the timestamp-only indexes on samples and rollup_1m (a third of the samples table's
size, and a second B-tree write per probe): purge and rollups now walk each target's primary
key range instead. Adds indexes for path tests by event/target, devices by target/group, and
open events.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_samples_ts", table_name="samples")
    op.drop_index("ix_rollup_1m_ts", table_name="rollup_1m")
    op.drop_index("ix_device_ips_ip", table_name="device_ips")
    op.create_index("ix_path_tests_event", "path_tests", ["event_id"])
    op.create_index("ix_path_tests_target_ts", "path_tests", ["target_id", "ts_ms"])
    op.create_index("ix_devices_target", "devices", ["target_id"])
    op.create_index("ix_devices_merged", "devices", ["merged_into"])
    op.create_index(
        "ix_events_open", "events", ["start_ms"], sqlite_where=sa.text("end_ms IS NULL")
    )


def downgrade() -> None:
    op.drop_index("ix_events_open", table_name="events")
    op.drop_index("ix_devices_merged", table_name="devices")
    op.drop_index("ix_devices_target", table_name="devices")
    op.drop_index("ix_path_tests_target_ts", table_name="path_tests")
    op.drop_index("ix_path_tests_event", table_name="path_tests")
    op.create_index("ix_device_ips_ip", "device_ips", ["ip"])
    op.create_index("ix_rollup_1m_ts", "rollup_1m", ["bucket_ts"])
    op.create_index("ix_samples_ts", "samples", ["ts_ms"])
