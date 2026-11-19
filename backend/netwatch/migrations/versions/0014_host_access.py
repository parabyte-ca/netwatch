"""v0.27: how to reach a device or a remote server (SSH user and port, admin page).

Replaces the v0.26 ``servers`` list: each saved server moves onto the device with its address.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19
"""

import json
import time

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column("ssh_user", sa.String(32), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_mode", sa.String(4), nullable=False, server_default="auto"),
        sa.Column("admin_url", sa.String(200), nullable=True),
        sa.CheckConstraint(
            "(device_id IS NULL) != (target_id IS NULL)", name="ck_host_access_owner"
        ),
    )
    conn = op.get_bind()
    now = int(time.time() * 1000)
    saved = conn.execute(
        sa.text("SELECT name, host, port, username FROM servers ORDER BY sort, name")
    ).all()
    for name, host, port, username in saved:
        row = conn.execute(
            sa.text(
                "SELECT COALESCE(d.merged_into, d.id) FROM devices d "
                "WHERE d.last_ip = :h "
                "OR d.id IN (SELECT device_id FROM device_ips WHERE ip = :h) "
                "ORDER BY d.last_seen_ms DESC LIMIT 1"
            ),
            {"h": host},
        ).first()
        if row is not None:
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO host_access (device_id, ssh_user, ssh_port, ssh_mode) "
                    "VALUES (:d, :u, :p, 'on')"
                ),
                {"d": row[0], "u": username, "p": port},
            )
        else:
            conn.execute(
                sa.text(
                    "INSERT INTO events (kind, severity, start_ms, end_ms, title, details) "
                    "VALUES ('security', 'info', :t, :t, :title, :details)"
                ),
                {
                    "t": now,
                    "title": f"Server {name} ({username}@{host}) was not moved: "
                    "it matches no device. Add it as a device or a remote server.",
                    "details": json.dumps({"action": "server_not_moved", "name": name}),
                },
            )
    op.drop_table("servers")


def downgrade() -> None:
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
    op.drop_table("host_access")
