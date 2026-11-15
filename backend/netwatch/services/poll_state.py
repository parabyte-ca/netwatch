"""One wording for "is this integration actually reading?" (Deco, Home Hub, AdGuard Home)."""

from __future__ import annotations


def poll_state(
    *, configured: bool, enabled: bool, paused: str | None, last_ok_ms: int | None
) -> str:
    """off: not set up; not_polling: saved but polling unticked; paused: stopped after errors;
    waiting: polling, nothing read yet; ok: reading."""
    if not configured:
        return "off"
    if not enabled:
        return "not_polling"
    if paused:
        return "paused"
    return "ok" if last_ok_ms else "waiting"
