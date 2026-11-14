"""Device groups and tags (v0.14).

A group ("Smart home", "Kids", one child's devices) has a colour and an icon; a device has at
most one group and any number of tags. Both live on the primary row of merged devices, so they
survive private-MAC changes, and devices with either are never purged as stale. Export and
import key devices by MAC, so a set-up survives a database rebuild.
"""

from __future__ import annotations

import re
import time

from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import device_groups, device_tags, devices

COLOURS = ("sky", "emerald", "amber", "rose", "violet", "cyan", "lime", "orange", "pink", "slate")
ICONS = ("home", "family", "child", "tv", "plug", "camera", "work", "game", "star", "shield")
TAG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")
MAX_GROUPS = 50
MAX_TAGS_PER_DEVICE = 12

# Suggested groups for ungrouped devices, by detected type: (name, colour, icon, types)
SUGGESTIONS = (
    ("Smart home", "emerald", "plug", ("iot", "camera", "speaker")),
    ("Family devices", "sky", "family", ("phone", "tablet", "laptop")),
    ("Entertainment", "violet", "tv", ("tv", "media_player", "game_console")),
    ("Home office", "amber", "work", ("printer", "nas", "server", "computer")),
)


class GroupError(Exception):
    """Refused change; the message is safe to show."""


def clean_tag(raw: str) -> str:
    t = str(raw or "").strip().lower().replace(" ", "-")
    if not TAG_RE.match(t):
        raise GroupError(f"{str(raw)[:30]!r}: tags are 1 to 24 letters, digits or hyphens")
    return t


def clean_name(raw: str) -> str:
    n = " ".join(str(raw or "").split())
    if not 1 <= len(n) <= 40:
        raise GroupError("group names are 1 to 40 characters")
    return n


class GroupStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ── groups ───────────────────────────────────────────────────────────────

    def list(self) -> list[dict]:
        counts = (
            select(devices.c.group_id, func.count().label("n"))
            .where(devices.c.merged_into.is_(None), devices.c.group_id.is_not(None))
            .group_by(devices.c.group_id)
            .subquery()
        )
        stmt = (
            select(device_groups, func.coalesce(counts.c.n, 0).label("devices"))
            .outerjoin(counts, counts.c.group_id == device_groups.c.id)
            .order_by(device_groups.c.sort, func.lower(device_groups.c.name))
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def get(self, group_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(device_groups).where(device_groups.c.id == group_id)).first()
        return dict(r._mapping) if r else None

    def by_name(self, name: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(device_groups).where(func.lower(device_groups.c.name) == name.lower())
            ).first()
        return dict(r._mapping) if r else None

    def _check(self, name: str, colour: str, icon: str, exclude: int | None = None) -> str:
        name = clean_name(name)
        if colour not in COLOURS:
            raise GroupError("unknown colour")
        if icon not in ICONS:
            raise GroupError("unknown icon")
        other = self.by_name(name)
        if other and other["id"] != exclude:
            raise GroupError(f"there is already a group called {other['name']}")
        return name

    def create(self, name: str, colour: str, icon: str) -> dict:
        name = self._check(name, colour, icon)
        with self.engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(device_groups)).scalar() >= MAX_GROUPS:
                raise GroupError(f"at most {MAX_GROUPS} groups")
            sort = conn.execute(select(func.coalesce(func.max(device_groups.c.sort), 0))).scalar()
            gid = conn.execute(
                insert(device_groups).values(
                    name=name,
                    colour=colour,
                    icon=icon,
                    sort=sort + 1,
                    created_ms=int(time.time() * 1000),
                )
            ).inserted_primary_key[0]
        return self.get(gid)

    def update(self, group_id: int, **values) -> dict:
        cur = self.get(group_id)
        if cur is None:
            raise GroupError("no such group")
        merged = {**cur, **{k: v for k, v in values.items() if v is not None}}
        name = self._check(merged["name"], merged["colour"], merged["icon"], exclude=group_id)
        with self.engine.begin() as conn:
            conn.execute(
                update(device_groups)
                .where(device_groups.c.id == group_id)
                .values(
                    name=name, colour=merged["colour"], icon=merged["icon"], sort=merged["sort"]
                )
            )
        return self.get(group_id)

    def delete(self, group_id: int) -> int:
        """Delete a group; its devices become ungrouped. Returns how many devices were in it."""
        with self.engine.begin() as conn:
            n = conn.execute(
                update(devices).where(devices.c.group_id == group_id).values(group_id=None)
            ).rowcount
            if not conn.execute(
                delete(device_groups).where(device_groups.c.id == group_id)
            ).rowcount:
                raise GroupError("no such group")
        return n

    # ── assignment ───────────────────────────────────────────────────────────

    def primaries(self, device_ids: list[int]) -> list[int]:
        """The primary row of each device (a merged device's group lives on its primary)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(devices.c.id, devices.c.merged_into).where(devices.c.id.in_(device_ids))
            ).all()
        found = {i for i, _ in rows}
        missing = set(device_ids) - found
        if missing:
            raise GroupError(f"no such device: {sorted(missing)[:5]}")
        return sorted({m or i for i, m in rows})

    def assign(self, device_ids: list[int], group_id: int | None) -> int:
        if group_id is not None and self.get(group_id) is None:
            raise GroupError("no such group")
        ids = self.primaries(device_ids)
        with self.engine.begin() as conn:
            return conn.execute(
                update(devices).where(devices.c.id.in_(ids)).values(group_id=group_id)
            ).rowcount

    def tags_of(self, device_ids: list[int]) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        if not device_ids:
            return out
        stmt = (
            select(device_tags.c.device_id, device_tags.c.tag)
            .where(device_tags.c.device_id.in_(device_ids))
            .order_by(device_tags.c.tag)
        )
        with self.engine.connect() as conn:
            for d, t in conn.execute(stmt):
                out.setdefault(d, []).append(t)
        return out

    def add_tags(self, device_ids: list[int], tags: list[str]) -> None:
        clean = sorted({clean_tag(t) for t in tags})
        ids = self.primaries(device_ids)
        have = self.tags_of(ids)
        for d in ids:
            if len(set(have.get(d, [])) | set(clean)) > MAX_TAGS_PER_DEVICE:
                raise GroupError(f"at most {MAX_TAGS_PER_DEVICE} tags per device")
        rows = [{"device_id": d, "tag": t} for d in ids for t in clean]
        if rows:
            with self.engine.begin() as conn:
                conn.execute(sqlite_insert(device_tags).values(rows).on_conflict_do_nothing())

    def remove_tags(self, device_ids: list[int], tags: list[str]) -> None:
        ids = self.primaries(device_ids)
        clean = [str(t).strip().lower() for t in tags]
        with self.engine.begin() as conn:
            conn.execute(
                delete(device_tags).where(
                    device_tags.c.device_id.in_(ids), device_tags.c.tag.in_(clean)
                )
            )

    def set_tags(self, device_id: int, tags: list[str]) -> list[str]:
        clean = sorted({clean_tag(t) for t in tags})
        if len(clean) > MAX_TAGS_PER_DEVICE:
            raise GroupError(f"at most {MAX_TAGS_PER_DEVICE} tags per device")
        [d] = self.primaries([device_id])
        with self.engine.begin() as conn:
            conn.execute(delete(device_tags).where(device_tags.c.device_id == d))
            if clean:
                conn.execute(insert(device_tags), [{"device_id": d, "tag": t} for t in clean])
        return clean

    def tags(self) -> list[dict]:
        stmt = (
            select(device_tags.c.tag, func.count().label("devices"))
            .group_by(device_tags.c.tag)
            .order_by(device_tags.c.tag)
        )
        with self.engine.connect() as conn:
            return [{"tag": t, "devices": n} for t, n in conn.execute(stmt)]

    # ── suggestions ──────────────────────────────────────────────────────────

    def suggestions(self) -> list[dict]:
        """Groups to propose for ungrouped devices, by detected type. Nothing is assigned."""
        stmt = select(devices).where(devices.c.merged_into.is_(None), devices.c.group_id.is_(None))
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt)]
        from .discovery.inventory import device_label  # avoid an import cycle

        out = []
        for name, colour, icon, types in SUGGESTIONS:
            members = [r for r in rows if (r["type_override"] or r["type"]) in types]
            if not members:
                continue
            existing = self.by_name(name)
            listed = [
                {
                    "id": r["id"],
                    "label": device_label(_parsed(r)),
                    "type": r["type_override"] or r["type"],
                }
                for r in members
            ]
            listed.sort(key=lambda d: d["label"].lower())
            out.append(
                {
                    "name": existing["name"] if existing else name,
                    "group_id": existing["id"] if existing else None,
                    "colour": existing["colour"] if existing else colour,
                    "icon": existing["icon"] if existing else icon,
                    "devices": listed,
                }
            )
        return out

    def accept(self, name: str, device_ids: list[int]) -> dict:
        group = self.by_name(clean_name(name))
        if group is None:
            spec = next((s for s in SUGGESTIONS if s[0].lower() == name.strip().lower()), None)
            colour, icon = (spec[1], spec[2]) if spec else ("slate", "star")
            group = self.create(name, colour, icon)
        self.assign(device_ids, group["id"])
        return group

    # ── export and import (devices by MAC) ───────────────────────────────────

    def export(self) -> dict:
        stmt = select(devices.c.id, devices.c.mac, devices.c.group_id).where(
            devices.c.merged_into.is_(None)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        tags = self.tags_of([r[0] for r in rows])
        groups = {g["id"]: g["name"] for g in self.list()}
        return {
            "netwatch": "device-groups",
            "version": 1,
            "groups": [{k: g[k] for k in ("name", "colour", "icon", "sort")} for g in self.list()],
            "devices": [
                {"mac": mac, "group": groups.get(gid), "tags": tags.get(did, [])}
                for did, mac, gid in rows
                if gid is not None or tags.get(did)
            ],
        }

    def import_(self, data: dict) -> dict:
        """Merge an export: create missing groups, assign by MAC, add tags. Never deletes."""
        if not isinstance(data, dict) or data.get("netwatch") != "device-groups":
            raise GroupError("not a NetWatch groups export")
        made = 0
        for g in data.get("groups") or []:
            if not self.by_name(clean_name(g.get("name", ""))):
                colour = g.get("colour") if g.get("colour") in COLOURS else "slate"
                icon = g.get("icon") if g.get("icon") in ICONS else "star"
                self.create(g["name"], colour, icon)
                made += 1
        with self.engine.connect() as conn:
            by_mac = {
                m.lower(): (i, merged)
                for i, m, merged in conn.execute(
                    select(devices.c.id, devices.c.mac, devices.c.merged_into)
                )
            }
        assigned, unknown = 0, []
        for d in data.get("devices") or []:
            hit = by_mac.get(str(d.get("mac", "")).lower())
            if hit is None:
                unknown.append(str(d.get("mac", ""))[:17])
                continue
            did = hit[1] or hit[0]
            if d.get("group"):
                g = self.by_name(clean_name(d["group"]))
                if g:
                    self.assign([did], g["id"])
            if d.get("tags"):
                self.add_tags([did], list(d["tags"])[:MAX_TAGS_PER_DEVICE])
            assigned += 1
        return {"groups_created": made, "devices": assigned, "unknown_macs": unknown}


def _parsed(r: dict) -> dict:
    import json

    d = dict(r)
    if isinstance(d.get("details"), str):
        try:
            d["details"] = json.loads(d["details"] or "{}")
        except ValueError:
            d["details"] = {}
    return d
