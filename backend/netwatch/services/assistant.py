"""Ask NetWatch: questions about the network in plain language, answered by Claude.

Claude gets read-only tools that call NetWatch's own GET API in-process (with the asking
user's session), so it sees exactly what the web UI shows and cannot change anything. The
Anthropic API key is stored encrypted with the key in ``data/.secret_key``; the feature stays
off until a key is saved. What the tools return (target names, device names and addresses,
incident history) is sent to Anthropic to answer the question.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic
import httpx

from .secrets import SecretBox
from .storage import Store

log = logging.getLogger(__name__)

CONFIG_KEY = "assistant_config"
API_KEY_KEY = "assistant_api_key"
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
EFFORTS = ("low", "medium", "high")
# Server-side refusal fallback: Claude API only, and not available for Haiku.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TURNS = 12  # model calls per question
MAX_TOOL_CHARS = 24_000  # per tool result
MAX_HISTORY = 20  # earlier messages kept per question
PER_HOUR = 40  # questions per hour (protects the bill)

SYSTEM = """You are Ask NetWatch, the assistant built into NetWatch, a self-hosted monitor for a \
home network. NetWatch pings and probes targets (the Home Hub, Deco mesh units, public servers, \
DNS resolvers), keeps an event timeline, diagnoses incidents layer by layer (TrueNAS link, \
switch, Home Hub, Deco mesh, Bell network, internet), discovers devices, measures per-device \
bandwidth through the Deco API, grades latency under load (bufferbloat), reads the Bell Home Hub \
4000 (fibre signal, uptime, port forwards) and scans for exposed services.

Answer the user's question about their network using the tools: look things up rather than \
guessing, and say so when the data does not cover the question. Lead with the answer, then the \
evidence (times, durations, which targets failed and which stayed healthy). When something is \
wrong, say what to check or change, and whether it looks like the Bell line, the home network \
or one device. Times from the tools are epoch milliseconds unless labelled; give times in the \
user's local time zone. Keep answers short and plain; use a short list when it helps. The tools \
only read data; you cannot change settings, restart anything or run tests."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    build: Callable[[dict], list[tuple[str, dict]]]  # input -> [(path, params)]
    shape: Callable[[list[Any]], Any] = lambda rs: rs[0] if len(rs) == 1 else rs
    required: tuple[str, ...] = ()

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


def _now_s() -> int:
    return int(time.time())


def _since(hours: Any, default: int, cap: int) -> int:
    try:
        h = int(hours)
    except (TypeError, ValueError):
        h = default
    return _now_s() - max(1, min(h, cap)) * 3600


def _status(rs: list[Any]) -> dict:
    status, targets = rs
    by_id = {t["id"]: t for t in targets}
    return {
        "targets": [
            {
                "name": by_id.get(s["id"], {}).get("name"),
                "host": by_id.get(s["id"], {}).get("host") or by_id.get(s["id"], {}).get("url"),
                "group": by_id.get(s["id"], {}).get("group"),
                **{k: v for k, v in s.items() if k not in ("id",)},
            }
            for s in status["targets"]
            if s.get("enabled")
        ]
    }


def _diagnosis(rs: list[Any]) -> dict:
    d = rs[0]
    return {"patterns": d["patterns"], "incidents": d["incidents"][:40]}


def _report(rs: list[Any]) -> dict:
    r = dict(rs[0])
    r.pop("markdown", None)
    r["incidents"] = r.get("incidents", [])[:30]
    return r


def _devices(rs: list[Any]) -> list[dict]:
    keep = ("id", "label", "type", "vendor", "last_ip", "online", "last_seen_ms", "macs")
    return [{k: d.get(k) for k in keep} for d in rs[0]["devices"]][:300]


def _line(rs: list[Any]) -> dict:
    hub, bloat, speed = rs
    return {
        "home_hub": {k: hub.get(k) for k in ("enabled", "last_ok_ms", "error", "snapshot")},
        "bufferbloat_tests": bloat.get("tests", [])[:10],
        "speed_tests": speed[:10] if isinstance(speed, list) else speed,
    }


TOOLS: tuple[Tool, ...] = (
    Tool(
        "network_status",
        "Live state of every monitored target right now: up/down/degraded, latency, packet "
        "loss. Start here for 'is the internet down?' style questions.",
        {},
        lambda i: [("/api/status", {}), ("/api/targets", {})],
        _status,
    ),
    Tool(
        "active_incidents",
        "Events still open right now (outages, packet loss, link problems).",
        {},
        lambda i: [("/api/events/active", {})],
    ),
    Tool(
        "timeline_events",
        "Timeline events over the last N hours. Kinds: outage, degraded, nic_link, nic_speed, "
        "nic_errors, monitor, anomaly, device, security, wifi, hub, exposure.",
        {
            "hours": {"type": "integer", "description": "How far back (1-720). Default 24."},
            "kinds": {"type": "array", "items": {"type": "string"}, "description": "Filter."},
        },
        lambda i: [
            (
                "/api/events",
                {
                    "start": _since(i.get("hours"), 24, 720),
                    "kind": i.get("kinds") or [],
                    "limit": 300,
                },
            )
        ],
    ),
    Tool(
        "diagnosis",
        "Incidents over the last N days, each with the failing layer, the likely causes "
        "(ranked with confidence) and evidence; plus recurring patterns.",
        {"days": {"type": "integer", "description": "1-90, default 7."}},
        lambda i: [("/api/diagnosis", {"start": _since((i.get("days") or 7) * 24, 168, 2160)})],
        _diagnosis,
    ),
    Tool(
        "isp_report",
        "The ISP evidence report for the last N days: availability, downtime attributed to the "
        "Bell network, per-target loss and latency, speed tests, Home Hub line evidence.",
        {"days": {"type": "integer", "description": "1-90, default 30."}},
        lambda i: [("/api/report", {"start": _since((i.get("days") or 30) * 24, 720, 2160)})],
        _report,
    ),
    Tool(
        "devices",
        "Devices discovered on the LAN (label, type, vendor, IP, online). Optional search.",
        {"query": {"type": "string", "description": "Name, IP, MAC or vendor to search for."}},
        lambda i: [("/api/devices", {"q": i["query"]} if i.get("query") else {})],
        _devices,
    ),
    Tool(
        "device_detail",
        "One device: identity, IP history, Wi-Fi details, open services, related events.",
        {"device_id": {"type": "integer"}},
        lambda i: [(f"/api/devices/{int(i['device_id'])}", {})],
        required=("device_id",),
    ),
    Tool(
        "bandwidth",
        "Who is using the bandwidth: live per-device rates (hours=0) or totals over N hours.",
        {"hours": {"type": "integer", "description": "0 = right now; else 1-720."}},
        lambda i: (
            [("/api/traffic/live", {})]
            if not i.get("hours")
            else [("/api/traffic/top", {"hours": max(1, min(int(i["hours"]), 720))})]
        ),
    ),
    Tool(
        "health_score",
        "The 0-100 network health score for the last N days and what lowers it.",
        {"days": {"type": "integer", "description": "1-90, default 7."}},
        lambda i: [("/api/score", {"days": max(1, min(int(i.get("days") or 7), 90))})],
    ),
    Tool(
        "exposure",
        "Latest exposure scan: open services on LAN devices, Home Hub port forwards, UPnP and "
        "DMZ, with findings.",
        {},
        lambda i: [("/api/exposure", {})],
    ),
    Tool(
        "line_and_speed",
        "The Bell line: Home Hub status (fibre signal in dBm, uptime, WAN), recent bufferbloat "
        "grades and speed tests.",
        {},
        lambda i: [
            ("/api/admin/hub", {}),
            ("/api/bufferbloat", {}),
            ("/api/iperf/tests", {"limit": 10}),
        ],
        _line,
    ),
    Tool(
        "path_tests",
        "Recent traceroutes (mtr) to public targets: per-hop loss and latency.",
        {},
        lambda i: [("/api/path-tests", {"limit": 10})],
    ),
)
BY_NAME = {t.name: t for t in TOOLS}


def _compact(value: Any) -> str:
    text = json.dumps(value, separators=(",", ":"), default=str)
    if len(text) > MAX_TOOL_CHARS:
        text = text[:MAX_TOOL_CHARS] + '…"[truncated: ask a narrower question]"'
    return text


class AskError(Exception):
    """A failure the user should see (no key, bad key, rate limit, Anthropic down)."""


@dataclass
class Answer:
    text: str
    tools: list[str] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None


class Assistant:
    def __init__(
        self,
        store: Store,
        secrets: SecretBox,
        app: Any,
        *,
        tz: Callable[[], str] = lambda: "America/Toronto",
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.app = app  # the ASGI app the tools call
        self.tz = tz
        self.client_factory = client_factory or (
            lambda key: anthropic.AsyncAnthropic(api_key=key, timeout=120.0, max_retries=2)
        )
        self._recent: deque[float] = deque()
        self._slots = asyncio.Semaphore(2)

    # ── configuration ────────────────────────────────────────────────────────

    def config(self) -> dict:
        c = self.store.kv_get(CONFIG_KEY, {}) or {}
        model = c.get("model") if c.get("model") in MODELS else MODELS[0]
        effort = c.get("effort") if c.get("effort") in EFFORTS else "medium"
        return {"enabled": bool(c.get("enabled", True)), "model": model, "effort": effort}

    def api_key(self) -> str | None:
        token = self.store.kv_get(API_KEY_KEY, None)
        return self.secrets.decrypt(token) if token else None

    def status(self) -> dict:
        return {
            **self.config(),
            "key_set": self.store.kv_get(API_KEY_KEY, None) is not None,
            "models": list(MODELS),
            "efforts": list(EFFORTS),
        }

    def save(self, enabled: bool, model: str, effort: str, api_key: str | None) -> None:
        self.store.kv_set(CONFIG_KEY, {"enabled": enabled, "model": model, "effort": effort})
        if api_key:
            self.store.kv_set(API_KEY_KEY, self.secrets.encrypt(api_key))

    def forget_key(self) -> None:
        self.store.kv_set(API_KEY_KEY, None)

    def available(self) -> bool:
        return self.config()["enabled"] and self.store.kv_get(API_KEY_KEY, None) is not None

    async def test(self, api_key: str | None = None) -> dict:
        key = api_key or await asyncio.to_thread(self.api_key)
        if not key:
            return {"ok": False, "error": "no API key saved"}
        model = (await asyncio.to_thread(self.config))["model"]
        client = self.client_factory(key)
        try:
            m = await client.models.retrieve(model)
        except anthropic.AuthenticationError:
            return {"ok": False, "error": "the API key was rejected"}
        except anthropic.PermissionDeniedError:
            return {"ok": False, "error": "the API key may not use this model"}
        except anthropic.NotFoundError:
            return {"ok": False, "error": f"model {model} is not available to this key"}
        except anthropic.APIConnectionError:
            return {"ok": False, "error": "could not reach api.anthropic.com"}
        except anthropic.APIStatusError as exc:
            return {"ok": False, "error": f"Anthropic API error {exc.status_code}"}
        finally:
            await client.close()
        return {"ok": True, "model": m.id, "display_name": getattr(m, "display_name", m.id)}

    # ── answering ────────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        now = time.monotonic()
        while self._recent and self._recent[0] < now - 3600:
            self._recent.popleft()
        if len(self._recent) >= PER_HOUR:
            raise AskError(f"limit of {PER_HOUR} questions an hour reached; try again later")
        self._recent.append(now)

    async def _call_tool(self, name: str, args: dict, cookies: dict) -> tuple[str, bool]:
        tool = BY_NAME.get(name)
        if tool is None:
            return f"unknown tool {name}", True
        try:
            calls = tool.build(args or {})
        except (KeyError, TypeError, ValueError) as exc:
            return f"bad input: {exc}", True
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost", cookies=cookies, timeout=60
        ) as http:
            results = []
            for path, params in calls:
                r = await http.get(path, params=params)
                if r.status_code >= 400:
                    detail = r.text[:300]
                    return f"{path} answered {r.status_code}: {detail}", True
                results.append(r.json())
        return _compact(tool.shape(results)), False

    async def ask(self, history: list[dict], cookies: dict) -> Answer:
        key = await asyncio.to_thread(self.api_key)
        cfg = await asyncio.to_thread(self.config)
        if not cfg["enabled"] or not key:
            raise AskError("Ask NetWatch is off: save an Anthropic API key in Admin first")
        self._throttle()
        zone = ZoneInfo(self.tz())
        now = datetime.now(zone)
        msgs: list[dict] = [
            {"role": m["role"], "content": m["content"]} for m in history[-MAX_HISTORY:]
        ]
        # The clock goes with the question, not the system prompt, so the prompt stays cached.
        msgs[-1] = {
            "role": "user",
            "content": f"{msgs[-1]['content']}\n\n(Now: {now:%Y-%m-%d %H:%M %Z}, "
            f"epoch ms {int(now.timestamp() * 1000)}, time zone {zone.key}.)",
        }
        model = cfg["model"]
        extra: dict[str, Any] = {}
        if model in FALLBACK_MODELS:
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        tools = [t.schema() for t in TOOLS]
        used: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        client = self.client_factory(key)
        try:
            async with self._slots:
                for _ in range(MAX_TURNS):
                    try:
                        resp = await client.beta.messages.create(
                            model=model,
                            max_tokens=16000,
                            system=SYSTEM,
                            tools=tools,
                            messages=msgs,
                            output_config={"effort": cfg["effort"]},
                            cache_control={"type": "ephemeral"},
                            **extra,
                        )
                    except anthropic.AuthenticationError:
                        raise AskError("the saved Anthropic API key was rejected") from None
                    except anthropic.RateLimitError:
                        raise AskError(
                            "Anthropic's rate limit was hit; try again shortly"
                        ) from None
                    except anthropic.APIConnectionError:
                        raise AskError("could not reach api.anthropic.com") from None
                    except anthropic.APIStatusError as exc:
                        log.warning("Anthropic API error", extra={"status": exc.status_code})
                        raise AskError(f"Anthropic API error {exc.status_code}") from None
                    for k in usage:
                        usage[k] += getattr(resp.usage, k, 0) or 0
                    if resp.stop_reason == "refusal":
                        return Answer(
                            "Claude declined to answer that question.",
                            used,
                            resp.model,
                            usage,
                            "refusal",
                        )
                    if resp.stop_reason == "pause_turn":
                        msgs.append({"role": "assistant", "content": resp.content})
                        continue
                    calls = [b for b in resp.content if b.type == "tool_use"]
                    if resp.stop_reason != "tool_use" or not calls:
                        text = "\n\n".join(b.text for b in resp.content if b.type == "text")
                        if resp.stop_reason == "max_tokens":
                            text += "\n\n(The answer was cut short.)"
                        return Answer(text.strip(), used, resp.model, usage, resp.stop_reason)
                    msgs.append({"role": "assistant", "content": resp.content})
                    results = []
                    for b in calls:
                        used.append(b.name)
                        out, err = await self._call_tool(b.name, b.input, cookies)
                        results.append(
                            {"type": "tool_result", "tool_use_id": b.id, "content": out}
                            | ({"is_error": True} if err else {})
                        )
                    msgs.append({"role": "user", "content": results})
        finally:
            await client.close()
        return Answer(
            "That took too many steps; try a narrower question.", used, model, usage, "max_turns"
        )
