"""Host NIC telemetry from sysfs: link state, flaps, speed/duplex changes and error counters.

The container shares the host's network namespace, so /sys/class/net shows the TrueNAS
interfaces. By default NetWatch watches the default-route interface and, when that is a
bridge, bond or VLAN, the physical ports underneath it, because a flapping cable shows up on
the physical port, not on the bridge.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import nic_stats
from .events import NIC_ERRORS, NIC_LINK, NIC_SPEED, EventLog

log = logging.getLogger(__name__)

COUNTERS = (
    "rx_bytes",
    "tx_bytes",
    "rx_packets",
    "tx_packets",
    "rx_errors",
    "tx_errors",
    "rx_dropped",
    "tx_dropped",
    "rx_crc_errors",
)
# Counters that indicate a physical or driver problem (drops are routine and only stored).
ERROR_COUNTERS = ("rx_errors", "tx_errors", "rx_crc_errors")
_VIRTUAL_PREFIXES = (
    "lo",
    "veth",
    "docker",
    "br-",
    "virbr",
    "tun",
    "tap",
    "wg",
    "kube",
    "cni",
    "flannel",
)


@dataclass(frozen=True)
class NicReading:
    iface: str
    operstate: str
    carrier: int | None
    speed: int | None  # Mb/s, None when unknown or link down
    duplex: str | None
    carrier_changes: int | None
    counters: dict[str, int]


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:  # EINVAL for speed/duplex while the link is down, ENOENT, ...
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def read_nic(sys_root: Path, iface: str) -> NicReading | None:
    base = sys_root / "class" / "net" / iface
    if not base.is_dir():
        return None
    speed = _int(_read(base / "speed"))
    stats = base / "statistics"
    return NicReading(
        iface=iface,
        operstate=_read(base / "operstate") or "unknown",
        carrier=_int(_read(base / "carrier")),
        speed=speed if speed and speed > 0 else None,
        duplex=_read(base / "duplex"),
        carrier_changes=_int(_read(base / "carrier_changes")),
        counters={c: _int(_read(stats / c)) or 0 for c in COUNTERS},
    )


def physical_members(sys_root: Path, iface: str, depth: int = 0) -> list[str]:
    """The interface itself if physical, else the physical devices beneath a bridge/bond/VLAN."""
    base = sys_root / "class" / "net" / iface
    if depth > 4 or not base.exists():
        return []
    lower: list[str] = []
    brif = base / "brif"
    if brif.is_dir():
        lower += sorted(p.name for p in brif.iterdir())
    slaves = _read(base / "bonding" / "slaves")
    if slaves:
        lower += slaves.split()
    lower += sorted(p.name[len("lower_") :] for p in base.glob("lower_*"))
    if not lower:
        return [iface] if (base / "device").exists() else []
    out: list[str] = []
    for name in lower:
        if name.startswith(_VIRTUAL_PREFIXES):
            continue
        for member in physical_members(sys_root, name, depth + 1):
            if member not in out:
                out.append(member)
    return out


def interfaces_to_watch(
    sys_root: Path, configured: list[str], default_iface: str | None
) -> list[str]:
    if configured:
        return configured
    if not default_iface:
        return []
    found = [default_iface, *physical_members(sys_root, default_iface)]
    return list(dict.fromkeys(found))


@dataclass
class _IfaceState:
    last: NicReading
    minute: int
    minute_start: NicReading
    errors_reported_minute: int = -1
    pending_errors: dict[str, int] = field(default_factory=dict)
    link_event_id: int | None = None


def _delta(new: int, old: int) -> int:
    return new - old if new >= old else new  # counter reset (driver reload / wrap)


class NicStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def write_minute(self, row: dict) -> None:
        stmt = sqlite_insert(nic_stats).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[nic_stats.c.iface, nic_stats.c.bucket_ts],
            set_={k: stmt.excluded[k] for k in row if k not in ("iface", "bucket_ts")},
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def history(self, iface: str, start_s: int, end_s: int) -> list[dict]:
        stmt = (
            select(nic_stats)
            .where(
                nic_stats.c.iface == iface,
                nic_stats.c.bucket_ts >= start_s,
                nic_stats.c.bucket_ts < end_s,
            )
            .order_by(nic_stats.c.bucket_ts)
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def purge(self, cutoff_s: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(nic_stats).where(nic_stats.c.bucket_ts < cutoff_s)).rowcount


class NicMonitor:
    """Polls sysfs, emits timeline events for link problems, stores per-minute counters."""

    def __init__(
        self,
        events: EventLog,
        store: NicStore,
        *,
        sys_root: Path,
        interfaces: Callable[[], list[str]],
        poll_s: float = 2.0,
    ) -> None:
        self.events = events
        self.store = store
        self.sys_root = sys_root
        self._interfaces = interfaces
        self.poll_s = poll_s
        self._state: dict[str, _IfaceState] = {}
        self._task: asyncio.Task | None = None
        self.watched: list[str] = []

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-nic")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.poll)
            except Exception:
                log.exception("NIC poll failed")
            await asyncio.sleep(self.poll_s)

    def status(self) -> list[dict]:
        out = []
        for iface in self.watched:
            st = self._state.get(iface)
            if st is None:
                out.append({"iface": iface, "present": False})
                continue
            r = st.last
            out.append(
                {
                    "iface": iface,
                    "present": True,
                    "operstate": r.operstate,
                    "carrier": r.carrier,
                    "speed": r.speed,
                    "duplex": r.duplex,
                    "carrier_changes": r.carrier_changes,
                    "counters": r.counters,
                    "link_down_since_event": st.link_event_id,
                }
            )
        return out

    def poll(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        ts_ms = int(now * 1000)
        minute = int(now // 60 * 60)
        self.watched = self._interfaces()
        for iface in self.watched:
            reading = read_nic(self.sys_root, iface)
            if reading is None:
                continue
            st = self._state.get(iface)
            if st is None:
                self._state[iface] = _IfaceState(reading, minute, reading)
                if reading.operstate == "down":
                    self._link_down(self._state[iface], reading, ts_ms)
                continue
            self._compare(st, reading, ts_ms)
            if minute != st.minute:
                self._flush_minute(st, reading)
                st.minute, st.minute_start = minute, reading
            st.last = reading

    # ── change detection ─────────────────────────────────────────────────────

    def _link_down(self, st: _IfaceState, r: NicReading, ts_ms: int) -> None:
        st.link_event_id = self.events.open(
            NIC_LINK,
            "critical",
            ts_ms,
            f"Link down on {r.iface}",
            iface=r.iface,
            details={"operstate": r.operstate, "carrier": r.carrier},
        )

    def _compare(self, st: _IfaceState, r: NicReading, ts_ms: int) -> None:
        prev = st.last
        was_up, is_up = prev.operstate == "up", r.operstate == "up"
        if was_up and not is_up:
            self._link_down(st, r, ts_ms)
        elif is_up and not was_up and st.link_event_id is not None:
            self.events.close(
                st.link_event_id, ts_ms, {"restored_speed": r.speed, "duplex": r.duplex}
            )
            st.link_event_id = None
        elif (
            was_up and is_up and prev.carrier_changes is not None and r.carrier_changes is not None
        ):
            flaps = r.carrier_changes - prev.carrier_changes
            if flaps > 0:  # down and back up again between two polls
                self.events.instant(
                    NIC_LINK,
                    "critical",
                    ts_ms,
                    f"Link flapped on {r.iface}",
                    iface=r.iface,
                    details={"carrier_transitions": flaps, "poll_s": self.poll_s},
                )

        if is_up and prev.speed and r.speed and (prev.speed, prev.duplex) != (r.speed, r.duplex):
            slower = r.speed < prev.speed
            self.events.instant(
                NIC_SPEED,
                "warning" if slower else "info",
                ts_ms,
                f"{r.iface} renegotiated {prev.speed} → {r.speed} Mb/s",
                iface=r.iface,
                details={
                    "from_speed": prev.speed,
                    "to_speed": r.speed,
                    "from_duplex": prev.duplex,
                    "to_duplex": r.duplex,
                },
            )

        for c in ERROR_COUNTERS:
            d = _delta(r.counters[c], prev.counters[c])
            if d:
                st.pending_errors[c] = st.pending_errors.get(c, 0) + d
        minute = ts_ms // 60000
        if st.pending_errors and st.errors_reported_minute != minute:
            total = sum(st.pending_errors.values())
            self.events.instant(
                NIC_ERRORS,
                "warning",
                ts_ms,
                f"{total} interface error{'s' if total != 1 else ''} on {r.iface}",
                iface=r.iface,
                details=dict(st.pending_errors),
            )
            st.pending_errors.clear()
            st.errors_reported_minute = minute

    def _flush_minute(self, st: _IfaceState, r: NicReading) -> None:
        start = st.minute_start
        row = {
            "iface": r.iface,
            "bucket_ts": st.minute,
            "operstate": st.last.operstate,
            "speed": st.last.speed,
            "duplex": st.last.duplex,
            "carrier_changes": _delta(r.carrier_changes or 0, start.carrier_changes or 0),
            **{c: _delta(r.counters[c], start.counters[c]) for c in COUNTERS},
        }
        try:
            self.store.write_minute(row)
        except Exception:
            log.exception("failed to store NIC minute", extra={"iface": r.iface})
