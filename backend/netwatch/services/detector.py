"""Per-target outage detection with hysteresis.

A target is declared **down** after ``down_after`` consecutive failed probes and only recovers
after ``up_after`` consecutive successes, so a single lost ping never opens an outage and a
single lucky reply never closes one. **Degraded** means intermittent packet loss: entered
(on a successful probe) when loss
over the sliding window reaches ``degraded_enter_pct`` and left only when it falls to
``degraded_exit_pct`` or below. Outage start and end times are the first failure and first
success of the deciding streaks, not the moment the threshold was crossed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..probes.base import ProbeResult

UNKNOWN, UP, DEGRADED, DOWN = "unknown", "up", "degraded", "down"
MIN_WINDOW_SAMPLES = 5


@dataclass(frozen=True)
class DetectorConfig:
    down_after: int = 3
    up_after: int = 3
    degraded_enter_pct: float = 5.0
    degraded_exit_pct: float = 1.0


@dataclass(frozen=True)
class Transition:
    target_id: int
    prev: str
    new: str
    ts_ms: int  # when the new state actually began
    loss_pct: float | None
    last_error: str | None


@dataclass
class _State:
    interval_s: float
    state: str = UNKNOWN
    since_ms: int | None = None
    fail_streak: int = 0
    fail_streak_start: int | None = None
    ok_streak: int = 0
    ok_streak_start: int | None = None
    last_error: str | None = None
    window: deque[tuple[int, bool]] = field(default_factory=deque)

    @property
    def window_ms(self) -> int:
        return int(max(60.0, 10 * self.interval_s) * 1000)


class OutageDetector:
    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()
        self._t: dict[int, _State] = {}

    def track(self, target_id: int, interval_s: float) -> None:
        st = self._t.get(target_id)
        if st is None:
            self._t[target_id] = _State(interval_s)
        else:
            st.interval_s = interval_s

    def forget(self, target_id: int) -> None:
        self._t.pop(target_id, None)

    def state(self, target_id: int) -> str:
        st = self._t.get(target_id)
        return st.state if st else UNKNOWN

    def since_ms(self, target_id: int) -> int | None:
        st = self._t.get(target_id)
        return st.since_ms if st else None

    def tracked(self) -> dict[int, str]:
        return {tid: st.state for tid, st in self._t.items()}

    @staticmethod
    def _loss_pct(st: _State) -> float | None:
        if len(st.window) < MIN_WINDOW_SAMPLES:
            return None
        lost = sum(1 for _, ok in st.window if not ok)
        return 100.0 * lost / len(st.window)

    def observe(self, r: ProbeResult) -> Transition | None:
        st = self._t.get(r.target_id)
        if st is None:
            return None
        cfg = self.config

        st.window.append((r.ts_ms, r.ok))
        cutoff = r.ts_ms - st.window_ms
        while st.window and st.window[0][0] < cutoff:
            st.window.popleft()

        if r.ok:
            if st.ok_streak == 0:
                st.ok_streak_start = r.ts_ms
            st.ok_streak += 1
            st.fail_streak = 0
            st.fail_streak_start = None
        else:
            if st.fail_streak == 0:
                st.fail_streak_start = r.ts_ms
            st.fail_streak += 1
            st.ok_streak = 0
            st.ok_streak_start = None
            st.last_error = r.error

        loss = self._loss_pct(st)
        new, began = st.state, r.ts_ms

        if st.state != DOWN and st.fail_streak >= cfg.down_after:
            new, began = DOWN, st.fail_streak_start or r.ts_ms
        elif st.state == DOWN:
            if st.ok_streak >= cfg.up_after:
                began = st.ok_streak_start or r.ts_ms
                # The window still holds the outage's failures; judge recovery on the streak.
                new = UP
        elif st.state == UNKNOWN and r.ok:
            new = UP
        elif st.state == UP and r.ok and loss is not None and loss >= cfg.degraded_enter_pct:
            # Judged on a success: intermittent loss between replies is "degraded"; an unbroken
            # run of failures is an outage in the making and must not flash "degraded" first.
            new = DEGRADED
        elif st.state == DEGRADED and loss is not None and loss <= cfg.degraded_exit_pct:
            new = UP

        if new == st.state:
            return None
        prev = st.state
        st.state, st.since_ms = new, began
        if new == UP and prev == DOWN:
            # Start the loss window afresh so the outage itself doesn't flag "degraded".
            st.window.clear()
        return Transition(r.target_id, prev, new, began, loss, st.last_error)
