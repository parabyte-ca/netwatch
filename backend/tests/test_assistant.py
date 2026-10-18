from __future__ import annotations

from types import SimpleNamespace as NS

import anthropic
import httpx

from netwatch.services.assistant import FALLBACK_BETA, TOOLS

KEY = "sk-ant-test-0123456789"


def block(type_: str, **kw) -> NS:
    return NS(type=type_, **kw)


def reply(content, stop="end_turn", model="claude-opus-5-5") -> NS:
    usage = NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=50)
    return NS(content=content, stop_reason=stop, model=model, usage=usage)


class FakeClaude:
    """Scripted replies; records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.beta = NS(messages=NS(create=self._create))
        self.models = NS(retrieve=self._retrieve)
        self.closed = 0
        self.fail: Exception | None = None

    async def _create(self, **kw):
        if self.fail:
            raise self.fail
        self.requests.append({**kw, "messages": list(kw["messages"])})  # as sent
        return self.script.pop(0)

    async def _retrieve(self, model):
        if self.fail:
            raise self.fail
        return NS(id=model, display_name="Claude Opus 5.5")

    async def close(self):
        self.closed += 1


def setup(client, script):
    fake = FakeClaude(script)
    a = client.app.state.assistant
    a.client_factory = lambda key: fake
    r = client.put(
        "/api/admin/assistant",
        json={"enabled": True, "model": "claude-opus-5-5", "effort": "medium", "api_key": KEY},
    )
    assert r.status_code == 200 and r.json()["key_set"] and KEY not in r.text
    return fake


def test_off_until_a_key_is_saved(client):
    assert client.get("/api/ask").json()["available"] is False
    r = client.post("/api/ask", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503 and "API key" in r.json()["detail"]
    bad = {"enabled": True, "model": "claude-opus-5-5", "effort": "medium", "api_key": "nope"}
    assert client.put("/api/admin/assistant", json=bad).status_code == 422
    bad = {"enabled": True, "model": "gpt-4", "effort": "medium"}
    assert client.put("/api/admin/assistant", json=bad).status_code == 422


def test_answers_with_read_only_tools(client):
    fake = setup(
        client,
        [
            reply(
                [
                    block("thinking", thinking=""),
                    block("tool_use", id="t1", name="network_status", input={}),
                    block("tool_use", id="t2", name="timeline_events", input={"hours": 6}),
                ],
                stop="tool_use",
            ),
            reply([block("text", text="Everything is up. No outages in the last 6 hours.")]),
        ],
    )
    assert client.get("/api/ask").json()["available"] is True
    r = client.post("/api/ask", json={"messages": [{"role": "user", "content": "Is it down?"}]})
    body = r.json()
    assert r.status_code == 200, body
    assert body["answer"].startswith("Everything is up")
    assert body["tools"] == ["network_status", "timeline_events"]
    assert body["usage"]["input_tokens"] == 200
    first, second = fake.requests
    assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default" and first["betas"] == [FALLBACK_BETA]
    assert "thinking" not in first  # Opus 5.5: adaptive thinking is always on
    assert "(Now: " in first["messages"][-1]["content"]
    assert {t["name"] for t in first["tools"]} == {t.name for t in TOOLS}
    # Both tool results come back in one user message, after the assistant turn as sent.
    assistant, results = second["messages"][-2:]
    assert assistant["role"] == "assistant" and assistant["content"][0].type == "thinking"
    assert [x["tool_use_id"] for x in results["content"]] == ["t1", "t2"]
    assert all(not x.get("is_error") for x in results["content"])
    assert '"targets"' in results["content"][0]["content"]
    assert fake.closed == 1


def test_tool_errors_refusals_and_haiku(client):
    fake = setup(
        client,
        [
            reply(
                [block("tool_use", id="t1", name="device_detail", input={"device_id": 999})],
                stop="tool_use",
            ),
            reply([block("text", text="No such device.")]),
            reply([], stop="refusal"),
        ],
    )
    r = client.post("/api/ask", json={"messages": [{"role": "user", "content": "Device 999?"}]})
    err = fake.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "404" in err["content"] and r.json()["answer"] == "No such device."
    r = client.post("/api/ask", json={"messages": [{"role": "user", "content": "?"}]})
    assert r.json()["stop_reason"] == "refusal"
    client.put("/api/admin/assistant",
               json={"enabled": True, "model": "claude-haiku-5-5", "effort": "low"})  # fmt: skip
    fake.script.append(reply([block("text", text="ok")], model="claude-haiku-5-5"))
    client.post("/api/ask", json={"messages": [{"role": "user", "content": "?"}]})
    assert "fallbacks" not in fake.requests[-1] and "betas" not in fake.requests[-1]


def test_key_test_and_api_errors(client):
    fake = setup(client, [])
    assert client.post("/api/admin/assistant/test", json={}).json()["ok"] is True
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake.fail = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=req), body=None
    )
    assert "rejected" in client.post("/api/admin/assistant/test", json={}).json()["error"]
    r = client.post("/api/ask", json={"messages": [{"role": "user", "content": "?"}]})
    assert r.status_code == 503 and "rejected" in r.json()["detail"]
    r = client.put(
        "/api/admin/assistant",
        json={"enabled": True, "model": "claude-opus-5-5", "effort": "medium", "forget_key": True},
    )
    assert r.json()["key_set"] is False
