"""Suggest "same machine" merges, e.g. after a network card swap gives a server a new MAC.

Devices are told apart by MAC, so a new card is a new device and the old card's row stays as
history. A suggestion pairs a device seen now (``new``) with an older one that has gone quiet
(``old``) when they report the same real hostname or the same address. Nothing is merged here:
the UI shows the pair and the owner accepts it (the existing merge) or dismisses it.
"""

from __future__ import annotations

from .classify import mergeable_name

DISMISSED_KEY = "device_merge_dismissed"
MAX_SUGGESTIONS = 20
MAX_DISMISSED = 500


def pair_key(a: int, b: int) -> str:
    lo, hi = sorted((int(a), int(b)))
    return f"{lo}:{hi}"


def _names(d: dict) -> set[str]:
    return {n for n in (mergeable_name(d.get("hostname")), mergeable_name(d.get("mdns_name"))) if n}


def _short(d: dict) -> dict:
    return {
        "id": d["id"],
        "label": d["label"],
        "online": d["online"],
        "last_seen_ms": d["last_seen_ms"],
        "last_ip": d.get("last_ip"),
        "monitored": d.get("target_id") is not None,
    }


def merge_suggestions(rows: list[dict], dismissed: set[str] | None = None) -> list[dict]:
    """Pairs from the device listing (primaries only): {"new", "old", "reasons", ...}.

    ``new`` is online now, ``old`` is offline and was last seen earlier. Two devices that are both
    online are never paired (two live network cards on one server is normal), Decos are skipped,
    and a pair the owner said is not the same is left out. Name and address together rank first.
    """
    dismissed = dismissed or set()
    live = [r for r in rows if r["online"] and not (r.get("details") or {}).get("is_deco")]
    gone = [r for r in rows if not r["online"] and not (r.get("details") or {}).get("is_deco")]
    out: list[dict] = []
    for new in live:
        for old in gone:
            if (
                old["last_seen_ms"] > new["last_seen_ms"]
                or pair_key(new["id"], old["id"]) in dismissed
            ):
                continue
            reasons = []
            shared = _names(new) & _names(old)
            if shared:
                reasons.append(f"same hostname ({sorted(shared)[0]})")
            if new.get("last_ip") and new["last_ip"] == old.get("last_ip"):
                reasons.append(f"same address ({new['last_ip']})")
            if not reasons:
                continue
            out.append(
                {
                    "new": _short(new),
                    "old": _short(old),
                    "reasons": reasons,
                    "both_monitored": bool(new.get("target_id") and old.get("target_id")),
                }
            )
    out.sort(key=lambda s: (-len(s["reasons"]), -s["old"]["last_seen_ms"], s["new"]["id"]))
    return out[:MAX_SUGGESTIONS]
