"""In-app updates: GitHub release checks, the automatic-update policy, and the request/status
protocol with the host-side upgrade agent (scripts/upgrade-agent.sh).

The container never updates itself. It writes ``request.json`` into a folder shared with the
host; the agent (a TrueNAS cron job) runs ``update.sh`` with the requested version, which backs
up the database, health-checks the new container and rolls back on failure. The agent reports
through ``agent.json`` (heartbeat), ``status.json``, ``upgrade.log`` and ``history.jsonl``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .. import __version__
from ..config import Settings
from .storage import Store

log = logging.getLogger(__name__)

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
MODES = ("off", "notify", "patch", "minor")
CHECK_EVERY_S = 24 * 3600
LOOP_EVERY_S = 600
WINDOW_MINUTES = 60
REQUEST_STALE_S = 3600
RUNNING_STALE_S = 1800
_MAX_FILE = 256 * 1024

POLICY_KEY = "update_policy"
CACHE_KEY = "update_cache"
AUTO_ATTEMPT_KEY = "update_auto_attempted"


# ── versions ──────────────────────────────────────────────────────────────────


def parse_version(v: str | None) -> tuple[int, int, int] | None:
    m = _SEMVER.match((v or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def bump_kind(current: str, target: str) -> str | None:
    """'major' | 'minor' | 'patch' when target is newer than current, else None."""
    c, t = parse_version(current), parse_version(target)
    if c is None or t is None or t <= c:
        return None
    if t[0] != c[0]:
        return "major"
    if t[1] != c[1]:
        return "minor"
    return "patch"


def allowed_by_policy(mode: str, kind: str | None) -> bool:
    return (mode == "patch" and kind == "patch") or (mode == "minor" and kind in ("patch", "minor"))


def in_window(now: datetime, window: str) -> bool:
    """True when local time `now` falls within WINDOW_MINUTES after HH:MM."""
    hh, mm = (int(x) for x in window.split(":"))
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for s in (start, start - timedelta(days=1)):
        if s <= now < s + timedelta(minutes=WINDOW_MINUTES):
            return True
    return False


# ── GitHub releases ───────────────────────────────────────────────────────────


def parse_releases(payload: list) -> list[dict]:
    out = []
    for r in payload:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        tag = str(r.get("tag_name") or "")
        if parse_version(tag) is None:
            continue
        out.append(
            {
                "version": tag.lstrip("v"),
                "tag": tag,
                "name": str(r.get("name") or tag)[:200],
                "notes": str(r.get("body") or "")[:20_000],
                "published_at": r.get("published_at"),
                "url": r.get("html_url"),
            }
        )
    out.sort(key=lambda x: parse_version(x["version"]), reverse=True)
    return out


async def fetch_releases(repo: str, client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 30},
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"NetWatch/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    resp.raise_for_status()
    return parse_releases(resp.json())


# ── shared folder with the host agent ─────────────────────────────────────────


def _parse_ts(value) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


class UpgradeFolder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_json(self, name: str) -> dict | None:
        p = self.path / name
        try:
            if p.is_symlink() or not p.is_file() or p.stat().st_size > _MAX_FILE:
                return None
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def agent(self) -> dict | None:
        return self._read_json("agent.json")

    def status(self) -> dict | None:
        return self._read_json("status.json")

    def request(self) -> dict | None:
        return self._read_json("request.json")

    def helper_connected(self, now: float) -> bool:
        agent = self.agent() or {}
        last = _parse_ts(agent.get("last_seen"))
        every = agent.get("every_minutes", 1)
        every = every if isinstance(every, int | float) and every > 0 else 1
        return last is not None and -60 <= now - last <= (2 * every + 1) * 60

    def write_request(self, action: str, version: str | None, reason: str, by: str) -> dict:
        req = {
            "id": uuid.uuid4().hex,
            "action": action,
            "version": version,
            "reason": reason,
            "requested_by": by,
            "requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.path.mkdir(parents=True, exist_ok=True)
        tmp = self.path / f".request.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(req))
        os.replace(tmp, self.path / "request.json")
        return req

    def log_tail(self, max_bytes: int = 32_768) -> str:
        p = self.path / "upgrade.log"
        try:
            if p.is_symlink():
                return ""
            size = p.stat().st_size
            with p.open("rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                    f.readline()
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def history(self, limit: int = 20) -> list[dict]:
        p = self.path / "history.jsonl"
        try:
            if p.is_symlink() or p.stat().st_size > 4 * _MAX_FILE:
                return []
            lines = p.read_text().splitlines()
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
            if len(out) >= limit:
                break
        return out

    def state(self, now: float) -> dict:
        """Derived progress: idle | requested | running | succeeded | failed."""
        req, st = self.request(), self.status()
        if req is not None:
            age = now - (_parse_ts(req.get("requested_at")) or now)
            if age > REQUEST_STALE_S:
                return {
                    "state": "failed",
                    "reason": "the upgrade agent never picked up the request",
                    "request": req,
                }
            return {"state": "requested", "request": req}
        if st is None:
            return {"state": "idle"}
        if st.get("state") == "running":
            started = _parse_ts(st.get("started_at")) or now
            if now - started > RUNNING_STALE_S:
                return {"state": "failed", "reason": "the update stalled", "status": st}
            return {"state": "running", "status": st}
        if st.get("state") in ("succeeded", "failed"):
            return {"state": st["state"], "status": st}
        return {"state": "idle"}


# ── manager ───────────────────────────────────────────────────────────────────


class UpdateError(Exception):
    pass


class UpdateManager:
    def __init__(
        self, settings: Settings, store: Store, *, client: httpx.AsyncClient | None = None
    ):
        self.settings = settings
        self.store = store
        self.folder = UpgradeFolder(settings.upgrade_dir) if settings.upgrade_dir else None
        self._client = client
        self._task: asyncio.Task | None = None
        self._check_lock = asyncio.Lock()
        self._last_manual_check = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.settings.tz)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    # ── policy ───────────────────────────────────────────────────────────────

    def policy(self) -> dict:
        stored = self.store.kv_get(POLICY_KEY, {}) or {}
        mode = stored.get("mode", self.settings.auto_update)
        window = stored.get("window", self.settings.auto_update_window)
        return {"mode": mode if mode in MODES else "notify", "window": window}

    def set_policy(self, mode: str, window: str) -> dict:
        if mode not in MODES:
            raise UpdateError(f"mode must be one of {', '.join(MODES)}")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", window):
            raise UpdateError("window must be HH:MM (24-hour)")
        self.store.kv_set(POLICY_KEY, {"mode": mode, "window": window})
        return self.policy()

    # ── release checks ───────────────────────────────────────────────────────

    def cache(self) -> dict:
        return self.store.kv_get(CACHE_KEY, {}) or {}

    async def check(self, *, manual: bool = False, now: float | None = None) -> dict:
        if not self.settings.update_check:
            raise UpdateError("update checks are disabled (NETWATCH_UPDATE_CHECK=false)")
        async with self._check_lock:
            now = time.time() if now is None else now
            if manual and now - self._last_manual_check < 30:
                return self.cache()
            if manual:
                self._last_manual_check = now
            client = self._client or httpx.AsyncClient(timeout=15, follow_redirects=True)
            try:
                releases = await fetch_releases(self.settings.update_repo, client)
                cache = {"checked_at": now, "releases": releases, "error": None}
            except (httpx.HTTPError, ValueError) as exc:
                old = self.cache()
                cache = {**old, "checked_at": now, "error": f"{type(exc).__name__}: {exc}"[:300]}
                log.warning("update check failed", extra={"error": cache["error"]})
            finally:
                if self._client is None:
                    await client.aclose()
            await asyncio.to_thread(self.store.kv_set, CACHE_KEY, cache)
            return cache

    # ── summary for the UI ───────────────────────────────────────────────────

    def summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        cache = self.cache()
        current = __version__
        releases = cache.get("releases") or []
        newer = [r for r in releases if bump_kind(current, r["version"])]
        latest = releases[0] if releases else None
        policy = self.policy()
        auto_target = self._auto_target(newer, policy["mode"])
        return {
            "current": current,
            "latest": latest["version"] if latest else None,
            "update_available": bool(newer),
            "latest_kind": bump_kind(current, newer[0]["version"]) if newer else None,
            "available": [{**r, "kind": bump_kind(current, r["version"])} for r in newer],
            "auto_target": auto_target["version"] if auto_target else None,
            "checked_at": cache.get("checked_at"),
            "check_error": cache.get("error"),
            "check_enabled": self.settings.update_check,
            "policy": policy,
            "timezone": self.settings.tz,
            "in_app_updates": self.folder is not None,
            "helper": self._helper(now),
            "progress": self.folder.state(now) if self.folder else {"state": "idle"},
            "history": self.folder.history() if self.folder else [],
        }

    def _helper(self, now: float) -> dict:
        if self.folder is None:
            return {"connected": False, "last_seen": None}
        agent = self.folder.agent() or {}
        return {
            "connected": self.folder.helper_connected(now),
            "last_seen": agent.get("last_seen"),
            "agent_version": agent.get("agent_version"),
        }

    @staticmethod
    def _auto_target(newer: list[dict], mode: str) -> dict | None:
        current = __version__
        for r in newer:  # newest first
            if allowed_by_policy(mode, bump_kind(current, r["version"])):
                return r
        return None

    # ── actions ──────────────────────────────────────────────────────────────

    def _ensure_ready(self, now: float) -> UpgradeFolder:
        if self.folder is None:
            raise UpdateError("in-app updates are disabled (no NETWATCH_UPGRADE_DIR)")
        if not self.folder.helper_connected(now):
            raise UpdateError(
                "the upgrade agent on the host is not connected; run "
                "`sudo scripts/upgrade-agent.sh --install` on TrueNAS"
            )
        state = self.folder.state(now)["state"]
        if state in ("requested", "running"):
            raise UpdateError("an update is already in progress")
        return self.folder

    def request_update(self, version: str, *, reason: str, by: str) -> dict:
        now = time.time()
        known = {r["version"] for r in self.cache().get("releases") or []}
        if version not in known:
            raise UpdateError(f"{version} is not a published release; check for updates first")
        if bump_kind(__version__, version) is None:
            raise UpdateError(f"{version} is not newer than the running {__version__}")
        req = self._ensure_ready(now).write_request("update", version, reason, by)
        log.info("update requested", extra={"version": version, "reason": reason, "by": by})
        return req

    def request_rollback(self, *, by: str) -> dict:
        req = self._ensure_ready(time.time()).write_request("rollback", None, "manual", by)
        log.info("rollback requested", extra={"by": by})
        return req

    # ── background loop ──────────────────────────────────────────────────────

    async def tick(self, now: float | None = None) -> dict | None:
        """One scheduler pass: daily release check, then automatic update if eligible."""
        now = time.time() if now is None else now
        if self.settings.update_check:
            checked = self.cache().get("checked_at") or 0
            if now - checked >= CHECK_EVERY_S:
                await self.check(now=now)
        return await asyncio.to_thread(self._maybe_auto_update, now)

    def _maybe_auto_update(self, now: float) -> dict | None:
        policy = self.policy()
        if policy["mode"] not in ("patch", "minor") or self.folder is None:
            return None
        newer = [
            r for r in self.cache().get("releases") or [] if bump_kind(__version__, r["version"])
        ]
        target = self._auto_target(newer, policy["mode"])
        if target is None:
            return None
        local_now = datetime.fromtimestamp(now, self._tz())
        if not in_window(local_now, policy["window"]):
            return None
        if self.store.kv_get(AUTO_ATTEMPT_KEY) == target["version"]:
            return None  # tried already (a failed attempt is not retried automatically)
        try:
            req = self.request_update(target["version"], reason="auto", by="auto-update")
        except UpdateError as exc:
            log.warning("automatic update skipped", extra={"reason": str(exc)})
            return None
        self.store.kv_set(AUTO_ATTEMPT_KEY, target["version"])
        return req

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-updates")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # let startup settle before the first check
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("update scheduler pass failed")
            await asyncio.sleep(LOOP_EVERY_S)
