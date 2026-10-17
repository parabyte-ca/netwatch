"""Optional Prometheus exposition (/metrics)."""

from __future__ import annotations

import contextlib

from prometheus_client import CollectorRegistry, Counter, Gauge

from ..probes.base import ProbeResult, TargetSpec


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        labels = ("target", "kind", "group")
        self.rtt = Gauge(
            "netwatch_probe_rtt_ms", "Last probe RTT in ms", labels, registry=self.registry
        )
        self.up = Gauge(
            "netwatch_probe_up", "1 if the last probe succeeded", labels, registry=self.registry
        )
        self.total = Counter(
            "netwatch_probes", "Probes executed", (*labels, "result"), registry=self.registry
        )
        self._labels: dict[int, tuple[str, str, str]] = {}

    def track(self, t: TargetSpec) -> None:
        self.forget(t.id)
        self._labels[t.id] = (t.name, t.kind, t.group)

    def forget(self, target_id: int) -> None:
        lbl = self._labels.pop(target_id, None)
        if lbl is None:
            return
        for metric in (self.rtt, self.up):
            with contextlib.suppress(KeyError):
                metric.remove(*lbl)
        for result in ("ok", "fail"):
            with contextlib.suppress(KeyError):
                self.total.remove(*lbl, result)

    def record(self, r: ProbeResult) -> None:
        lbl = self._labels.get(r.target_id)
        if lbl is None:
            return
        self.up.labels(*lbl).set(1 if r.ok else 0)
        if r.rtt_ms is not None:
            self.rtt.labels(*lbl).set(r.rtt_ms)
        self.total.labels(*lbl, "ok" if r.ok else "fail").inc()
