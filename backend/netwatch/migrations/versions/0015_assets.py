"""v0.32: network assets on the map (unmanaged switches) and what is pinned behind them.

The map's fixed, inferred "Switch" tile (Hub to Switch to TrueNAS) becomes the first switch:
asset 1, with TrueNAS pinned behind it, and its saved map position and link-side pin move from the
node id ``switch`` to ``asset:1``.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-27
"""

import json
import time

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

LAYOUT_KEY = "map_layout"


def _rename(conn, old: str, new: str) -> None:
    raw = conn.execute(sa.text("SELECT value FROM kv WHERE key = :k"), {"k": LAYOUT_KEY}).scalar()
    if not raw:
        return
    layout = json.loads(raw)
    if not isinstance(layout, dict):
        return
    for part in ("positions", "pins"):
        block = layout.get(part)
        if isinstance(block, dict) and old in block:
            block[new] = block.pop(old)
    conn.execute(
        sa.text("UPDATE kv SET value = :v WHERE key = :k"),
        {"v": json.dumps(layout), "k": LAYOUT_KEY},
    )


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="switch"),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("uplink", sa.String(80), nullable=False, server_default="hub"),
        sa.Column("notes", sa.String(200), nullable=True),
        sa.Column("created_ms", sa.Integer(), nullable=False),
    )
    op.create_table(
        "node_parents",
        sa.Column("node_key", sa.String(40), primary_key=True),
        sa.Column("parent", sa.String(80), nullable=False),
        sqlite_with_rowid=False,
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO assets (id, kind, name, uplink, created_ms) "
            "VALUES (1, 'switch', 'Switch', 'hub', :t)"
        ),
        {"t": int(time.time() * 1000)},
    )
    conn.execute(
        sa.text("INSERT INTO node_parents (node_key, parent) VALUES ('truenas', 'asset:1')")
    )
    _rename(conn, "switch", "asset:1")


def downgrade() -> None:
    _rename(op.get_bind(), "asset:1", "switch")
    op.drop_table("node_parents")
    op.drop_table("assets")
