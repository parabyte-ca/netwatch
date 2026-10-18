"""Admin authentication: scrypt password hashes, server-side sessions, login throttling.

Every /api/* route requires a session except the small allowlist below. Sessions live in an
HttpOnly, SameSite=Strict cookie; state-changing requests must also carry the
``X-Requested-With: netwatch`` header, which a cross-site form or image cannot send.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, delete, func, insert, select, update

from .db import sessions, users

log = logging.getLogger(__name__)

COOKIE_NAME = "netwatch_session"
SETUP_TOKEN_FILE = "setup-token"  # noqa: S105 - a file name
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "netwatch"
MIN_PASSWORD_LEN = 10

# scrypt parameters: ~16 MB and ~50 ms per hash on a modest CPU.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def token_hash(token: str) -> str:
    return _token_hash(token)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SessionUser:
    user_id: int
    username: str


class AuthStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ── users ────────────────────────────────────────────────────────────────

    def user_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(users)).scalar_one()

    def create_user(self, username: str, password: str) -> int:
        now = time.time()
        with self.engine.begin() as conn:
            return conn.execute(
                insert(users).values(
                    username=username,
                    password_hash=hash_password(password),
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]

    def create_first_user(self, username: str, password: str) -> int | None:
        """Create the initial admin atomically; returns None if one already exists."""
        with self.engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(users)).scalar_one():
                return None
            now = time.time()
            return conn.execute(
                insert(users).values(
                    username=username,
                    password_hash=hash_password(password),
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]

    def authenticate(self, username: str, password: str) -> SessionUser | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users.c.id, users.c.username, users.c.password_hash).where(
                    func.lower(users.c.username) == username.lower()
                )
            ).first()
        if row is None:
            # Spend the same time as a real check so usernames can't be probed by timing.
            verify_password(password, _DUMMY_HASH)
            return None
        return (
            SessionUser(row.id, row.username)
            if verify_password(password, row.password_hash)
            else None
        )

    def set_password(self, user_id: int, password: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(password_hash=hash_password(password), updated_at=time.time())
            )
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))

    def set_password_by_name(self, username: str, password: str) -> bool:
        with self.engine.connect() as conn:
            uid = conn.execute(
                select(users.c.id).where(func.lower(users.c.username) == username.lower())
            ).scalar()
        if uid is None:
            return False
        self.set_password(uid, password)
        return True

    # ── sessions ─────────────────────────────────────────────────────────────

    def create_session(self, user_id: int, days: int, ip: str | None, ua: str | None) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time() * 1000)
        with self.engine.begin() as conn:
            conn.execute(
                insert(sessions).values(
                    token_hash=_token_hash(token),
                    user_id=user_id,
                    created_ms=now,
                    last_seen_ms=now,
                    expires_ms=now + days * 86_400_000,
                    ip=(ip or "")[:64],
                    user_agent=(ua or "")[:200],
                )
            )
        return token

    def resolve_session(self, token: str, idle_s: int = 0) -> SessionUser | None:
        """The session's user, or None if it is unknown, expired or idle for over ``idle_s``."""
        now = int(time.time() * 1000)
        th = _token_hash(token)
        with self.engine.connect() as conn:
            row = conn.execute(
                select(sessions.c.user_id, sessions.c.last_seen_ms, users.c.username)
                .join(users, users.c.id == sessions.c.user_id)
                .where(sessions.c.token_hash == th, sessions.c.expires_ms > now)
            ).first()
        if row is None:
            return None
        if idle_s and now - row.last_seen_ms > idle_s * 1000:
            with self.engine.begin() as conn:
                conn.execute(delete(sessions).where(sessions.c.token_hash == th))
            return None
        if now - row.last_seen_ms > 60_000:  # throttle last-seen writes
            with self.engine.begin() as conn:
                conn.execute(
                    update(sessions).where(sessions.c.token_hash == th).values(last_seen_ms=now)
                )
        return SessionUser(row.user_id, row.username)

    def delete_session(self, token: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == _token_hash(token)))

    def list_sessions(self) -> list[dict]:
        now = int(time.time() * 1000)
        stmt = (
            select(sessions, users.c.username)
            .join(users, users.c.id == sessions.c.user_id)
            .where(sessions.c.expires_ms > now)
            .order_by(sessions.c.last_seen_ms.desc())
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def delete_session_hash(self, token_hash: str) -> bool:
        with self.engine.begin() as conn:
            return (
                conn.execute(delete(sessions).where(sessions.c.token_hash == token_hash)).rowcount
                > 0
            )

    def delete_other_sessions(self, keep_token: str) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(sessions).where(sessions.c.token_hash != _token_hash(keep_token))
            ).rowcount

    def purge_sessions(self) -> int:
        now = int(time.time() * 1000)
        with self.engine.begin() as conn:
            return conn.execute(delete(sessions).where(sessions.c.expires_ms <= now)).rowcount


_DUMMY_HASH = hash_password(secrets.token_hex(8))


class LoginThrottle:
    """Per-client lockout after repeated failures (in memory; resets on restart).

    An attempt is counted when it starts (``begin``), not when its password check fails, so
    a burst of parallel requests cannot all slip in before the first failure is recorded.
    """

    MAX_KEYS = 10_000  # bounds memory if source addresses rotate (IPv6)

    def __init__(
        self, max_failures: int = 5, window_s: float = 300, lockout_s: float = 300
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._inflight: dict[str, int] = defaultdict(int)
        self._refusal_logged: dict[str, float] = {}

    def retry_after(self, key: str, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        until = self._locked_until.get(key, 0)
        return max(0, int(until - now + 0.999))

    def begin(self, key: str, now: float | None = None) -> int:
        """Reserve an attempt: 0 to go ahead (then call ``end``), else seconds to wait."""
        now = time.monotonic() if now is None else now
        if wait := self.retry_after(key, now):
            return wait
        q = self._fails.get(key)
        recent = sum(1 for t in q if t >= now - self.window_s) if q else 0
        if recent + self._inflight[key] >= self.max_failures:
            return 1  # the attempts already under way would use up the allowance
        self._inflight[key] += 1
        return 0

    def end(self, key: str) -> None:
        self._inflight[key] -= 1
        if self._inflight[key] <= 0:
            del self._inflight[key]

    def failure(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if len(self._fails) >= self.MAX_KEYS and key not in self._fails:
            self._prune(now)
        q = self._fails[key]
        q.append(now)
        while q and q[0] < now - self.window_s:
            q.popleft()
        if len(q) >= self.max_failures:
            self._locked_until[key] = now + self.lockout_s
            q.clear()

    def success(self, key: str) -> None:
        self._fails.pop(key, None)
        self._locked_until.pop(key, None)

    def log_refusal(self, key: str, now: float | None = None) -> bool:
        """True once per lockout, so refused attempts don't flood the security log."""
        now = time.monotonic() if now is None else now
        until = self._locked_until.get(key, 0)
        if self._refusal_logged.get(key) == until:
            return False
        self._refusal_logged[key] = until
        return True

    def _prune(self, now: float) -> None:
        for k in [k for k, q in self._fails.items() if not q or q[-1] < now - self.window_s]:
            del self._fails[k]
        for k in [k for k, t in self._locked_until.items() if t < now]:
            self._locked_until.pop(k, None)
            self._refusal_logged.pop(k, None)
        while len(self._fails) >= self.MAX_KEYS:  # still full: drop the oldest
            del self._fails[next(iter(self._fails))]


def issue_setup_token(store: AuthStore, data_dir: Path) -> str | None:
    """While no admin exists, a one-time code that first-run setup must present.

    Without it, anyone on the LAN who opened NetWatch first could claim the admin account.
    The code is written to ``data/setup-token`` (root-only) and to the log; setup.sh prints it.
    """
    path = data_dir / SETUP_TOKEN_FILE
    if store.user_count():
        path.unlink(missing_ok=True)
        return None
    token = "-".join(secrets.token_hex(3) for _ in range(3))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    log.warning("First-run setup code: %s (also in data/%s)", token, SETUP_TOKEN_FILE)
    return token


def setup_token_ok(expected: str | None, given: str | None) -> bool:
    if not expected:
        return True
    return hmac.compare_digest((given or "").strip().lower().encode(), expected.encode())


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"password must be at least {MIN_PASSWORD_LEN} characters"
    if len(password) > 256:
        return "password is too long"
    return None
