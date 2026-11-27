"""Network assets drawn on the map: unmanaged switches, and what is pinned behind them.

An unmanaged switch has no address and sends nothing of its own, so NetWatch cannot see it. It is
a note on the map that you draw: it plugs into the Hub, a Deco or another switch, and devices (and
the NetWatch host) are pinned behind it. Map only: the diagnosis never reads these.
"""

from __future__ import annotations

import re
import time

from sqlalchemy import Engine, delete, func, insert, select, update

from ..db import assets, node_parents

KINDS = ("switch",)
MAX_ASSETS = 30
MAX_LINKS = 500
MAX_DEPTH = 8
_UPLINK = re.compile(r"^(hub|deco:[0-9a-fA-F:-]{11,17}|asset:\d{1,9})$")
_PARENT = re.compile(r"^(hub|deco:[0-9a-fA-F:-]{11,17}|asset:\d{1,9})$")
_NODE = re.compile(r"^(truenas|dev:\d{1,9})$")


class AssetError(ValueError):
    pass


def asset_node(asset_id: int) -> str:
    return f"asset:{asset_id}"


class AssetStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ── reads ────────────────────────────────────────────────────────────────

    def list(self) -> list[dict]:
        """Every asset (name order) with the node keys pinned directly behind it."""
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(select(assets).order_by(assets.c.id))]
            links = conn.execute(select(node_parents)).all()
        behind: dict[str, list[str]] = {}
        for key, parent in links:
            behind.setdefault(parent, []).append(key)
        for a in rows:
            a["members"] = sorted(behind.get(asset_node(a["id"]), []))
        rows.sort(key=lambda a: a["name"].lower())
        return rows

    def get(self, asset_id: int) -> dict | None:
        return next((a for a in self.list() if a["id"] == asset_id), None)

    def links(self) -> dict[str, str]:
        """{node key: parent node id} for everything pinned."""
        with self.engine.connect() as conn:
            return {k: p for k, p in conn.execute(select(node_parents)).all()}

    # ── validation ───────────────────────────────────────────────────────────

    @staticmethod
    def _name(raw: str) -> str:
        name = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not 1 <= len(name) <= 40:
            raise AssetError("give the switch a name of 1 to 40 characters")
        return name

    @staticmethod
    def _unique(conn, name: str, own: int | None) -> None:
        stmt = select(assets.c.id).where(func.lower(assets.c.name) == name.lower())
        if own is not None:
            stmt = stmt.where(assets.c.id != own)
        if conn.execute(stmt).first() is not None:
            raise AssetError(f"there is already a switch called {name}")

    @staticmethod
    def _check_uplink(conn, uplink: str, own: int | None) -> str:
        if not _UPLINK.match(uplink or ""):
            raise AssetError("a switch plugs into the Hub, a Deco or another switch")
        if uplink.startswith("asset:"):
            cur: int | None = int(uplink[6:])
            if conn.execute(select(assets.c.id).where(assets.c.id == cur)).first() is None:
                raise AssetError("that switch does not exist")
            seen: set[int] = set()
            while cur is not None:  # follow the chain up: it must not come back to this switch
                if cur == own or cur in seen or len(seen) > MAX_DEPTH:
                    raise AssetError("switches cannot plug into each other in a loop")
                seen.add(cur)
                up = conn.execute(select(assets.c.uplink).where(assets.c.id == cur)).scalar()
                cur = int(up[6:]) if up and up.startswith("asset:") else None
        return uplink

    # ── writes ───────────────────────────────────────────────────────────────

    def create(self, name: str, uplink: str = "hub", notes: str | None = None) -> dict:
        name = self._name(name)
        with self.engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(assets)).scalar() >= MAX_ASSETS:
                raise AssetError(f"at most {MAX_ASSETS} switches")
            self._unique(conn, name, None)
            uplink = self._check_uplink(conn, uplink, None)
            new_id = conn.execute(
                insert(assets).values(
                    kind="switch",
                    name=name,
                    uplink=uplink,
                    notes=(notes or "").strip()[:200] or None,
                    created_ms=int(time.time() * 1000),
                )
            ).inserted_primary_key[0]
        return self.get(new_id)  # type: ignore[return-value]

    def update(self, asset_id: int, values: dict) -> dict:
        with self.engine.begin() as conn:
            if conn.execute(select(assets.c.id).where(assets.c.id == asset_id)).first() is None:
                raise AssetError("no such switch")
            out: dict = {}
            if "name" in values:
                out["name"] = self._name(values["name"])
                self._unique(conn, out["name"], asset_id)
            if "uplink" in values:
                out["uplink"] = self._check_uplink(conn, values["uplink"], asset_id)
            if "notes" in values:
                out["notes"] = (values["notes"] or "").strip()[:200] or None
            if out:
                conn.execute(update(assets).where(assets.c.id == asset_id).values(**out))
        return self.get(asset_id)  # type: ignore[return-value]

    def delete(self, asset_id: int) -> dict:
        """Remove a switch. What was behind it (devices, other switches) moves to its uplink."""
        with self.engine.begin() as conn:
            row = conn.execute(select(assets).where(assets.c.id == asset_id)).first()
            if row is None:
                raise AssetError("no such switch")
            me, up = asset_node(asset_id), row.uplink
            conn.execute(update(assets).where(assets.c.uplink == me).values(uplink=up))
            if up == "hub":  # the Hub is every node's default parent: the pin simply goes
                conn.execute(delete(node_parents).where(node_parents.c.parent == me))
            else:
                conn.execute(
                    update(node_parents).where(node_parents.c.parent == me).values(parent=up)
                )
            conn.execute(delete(assets).where(assets.c.id == asset_id))
        return dict(row._mapping)

    def assign(self, node_keys: list[str], parent: str | None) -> int:
        """Pin nodes behind a switch, a Deco or the Hub (``parent``), or unpin them (``None``).

        ``parent`` is ``asset:<id>`` of an existing switch, ``deco:<mac>`` (the caller checks that
        the Deco exists) or ``hub``. Returns how many nodes changed.
        """
        keys = list(dict.fromkeys(node_keys))
        if any(not _NODE.match(k) for k in keys):
            raise AssetError("only devices and the NetWatch host can be pinned")
        changed = 0
        with self.engine.begin() as conn:
            if parent is not None:
                if not _PARENT.match(parent):
                    raise AssetError("pin behind a switch, a Deco or the Home Hub")
                if (
                    parent.startswith("asset:")
                    and conn.execute(
                        select(assets.c.id).where(assets.c.id == int(parent[6:]))
                    ).first()
                    is None
                ):
                    raise AssetError("that switch does not exist")
            have = {k: p for k, p in conn.execute(select(node_parents)).all()}
            for k in keys:
                if parent is None:
                    if k in have:
                        conn.execute(delete(node_parents).where(node_parents.c.node_key == k))
                        changed += 1
                elif have.get(k) != parent:
                    if k not in have and len(have) >= MAX_LINKS:
                        raise AssetError(f"at most {MAX_LINKS} pinned devices")
                    conn.execute(delete(node_parents).where(node_parents.c.node_key == k))
                    conn.execute(insert(node_parents).values(node_key=k, parent=parent))
                    have[k] = parent
                    changed += 1
        return changed

    def forget_device(self, device_id: int) -> None:
        """A deleted device takes its pin with it."""
        with self.engine.begin() as conn:
            conn.execute(delete(node_parents).where(node_parents.c.node_key == f"dev:{device_id}"))

    def move_pin(self, src: int, dst: int) -> None:
        """Two rows merged into one device: its pin follows to the surviving row."""
        with self.engine.begin() as conn:
            row = conn.execute(
                select(node_parents.c.parent).where(node_parents.c.node_key == f"dev:{src}")
            ).first()
            if row is None:
                return
            conn.execute(delete(node_parents).where(node_parents.c.node_key == f"dev:{src}"))
            if (
                conn.execute(
                    select(node_parents.c.parent).where(node_parents.c.node_key == f"dev:{dst}")
                ).first()
                is None
            ):
                conn.execute(insert(node_parents).values(node_key=f"dev:{dst}", parent=row[0]))


WIRED_HINT_MIN = 3  # a Deco with this many wired devices probably has a switch on a port


def hints(rows: list[dict], decos: dict[str, dict], pins: dict[str, str]) -> dict:
    """Where a switch probably is. Only suggestions: nothing is ever moved by itself.

    ``rows`` is the device listing (primary rows). ``unclaimed`` are online devices no Deco
    reported (so on the Hub's own ports or behind a switch), ``decos`` are Decos with several wired
    devices (more than their Ethernet ports usually means a switch is plugged in).
    """
    online = [
        d
        for d in rows
        if d["online"]
        and not (d.get("details") or {}).get("is_deco")
        and d.get("type") != "router"
        and d.get("type_reason") != "this NetWatch host"
        and f"dev:{d['id']}" not in pins
    ]
    unclaimed = sorted(
        (
            {"id": d["id"], "label": d["label"]}
            for d in online
            if not (d.get("details") or {}).get("deco_mac")
        ),
        key=lambda x: x["label"].lower(),
    )
    by_deco: dict[str, list[dict]] = {}
    for d in online:
        det = d.get("details") or {}
        if det.get("deco_mac") in decos and det.get("connection") == "wired":
            by_deco.setdefault(det["deco_mac"], []).append({"id": d["id"], "label": d["label"]})
    wired = [
        {
            "deco_mac": mac,
            "deco": f"deco:{mac}",
            "name": f"Deco {decos[mac].get('name') or mac}",
            "devices": sorted(ds, key=lambda x: x["label"].lower()),
        }
        for mac, ds in by_deco.items()
        if len(ds) >= WIRED_HINT_MIN
    ]
    wired.sort(key=lambda x: x["name"].lower())
    return {"unclaimed": unclaimed, "decos": wired}
