"""Regression tests for review findings 1-5.

Each test drives the real server against a fake upstream (httpx.MockTransport),
so routing, breakers, guard and fallback interact exactly as in production.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.guard.test_integrity import TestIntegrityGuard
from src.router.extractor import extract_routing_context
from src.server.app import create_app

TB = (
    "Traceback (most recent call last):\n"
    '  File "/app/service.py", line 10, in <module>\n'
    "ValueError: boom"
)


@pytest.fixture(autouse=True)
def _upstream_env(monkeypatch):
    """Resolve config placeholders without relying on a local .env file."""
    monkeypatch.setenv("ANTIGRAVITY_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setenv("ANTIGRAVITY_API_KEY", "test-key")
    monkeypatch.setenv("OPUS_4_6_MODEL_ID", "ag/opus")
    monkeypatch.setenv("SONNET_4_6_MODEL_ID", "ag/sonnet")
    monkeypatch.setenv("GEMINI_3_1_PRO_MODEL_ID", "ag/pro")
    monkeypatch.setenv("GEMINI_3_8_FLASH_MODEL_ID", "ag/flash")


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    )


class FakeUpstream:
    """Records every outbound payload; `respond(model_id, payload)` decides the reply."""

    def __init__(self, respond=None):
        self.calls: list[dict] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.calls.append(payload)
        if self._respond:
            return self._respond(payload["model"], payload)
        return _ok(request)

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]


def make_client(fake: FakeUpstream):
    app = create_app(None)
    app.state.upstream.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return app, TestClient(app)


def tool_turn(call_id: str, name: str, result: str, arguments: dict | None = None) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {})}}
        ]},
        {"role": "tool", "tool_call_id": call_id, "name": name, "content": result},
    ]


# ── Bug 1: a traceback buried in history must not count as the current failure ──

def _history_with_fixed_traceback(clean_turns: int) -> list[dict]:
    msgs = [{"role": "user", "content": "jalankan test"}]
    msgs += tool_turn("t0", "terminal", TB)
    for i in range(clean_turns):
        msgs += tool_turn(f"c{i}", "terminal", "5 passed")
    return msgs


def test_extractor_ignores_traceback_outside_tail():
    ctx = extract_routing_context({"messages": _history_with_fixed_traceback(clean_turns=3)})
    assert ctx.metadata.last_traceback is None


def test_extractor_still_reports_traceback_in_tail():
    ctx = extract_routing_context({"messages": _history_with_fixed_traceback(clean_turns=0)})
    assert ctx.metadata.last_traceback is not None
    assert "ValueError: boom" in ctx.metadata.last_traceback


def test_fixed_traceback_does_not_trip_identical_error_breaker():
    app, client = make_client(FakeUpstream())
    msgs = _history_with_fixed_traceback(clean_turns=3)
    for _ in range(4):
        r = client.post("/v1/chat/completions", json={
            "model": "auto", "messages": msgs, "metadata": {"session_id": "bug1"}
        })
        assert r.status_code == 200
    state = app.state.circuit_tracker.subtasks["bug1"]
    assert state.consecutive_identical_tracebacks == 0
    assert state.iterations == 0


# ── Bug 2: one Opus call must count once; handoff only after Opus fails twice ──

def test_single_opus_call_counts_once():
    app, client = make_client(FakeUpstream())
    # Previous turn served by tactical; the escalation request carries tactical's failure.
    app.state.circuit_tracker.record_served("bug2a", "gemini_tactical")
    r = client.post("/v1/chat/completions", json={
        "model": "opus_apex",
        "messages": [{"role": "user", "content": TB}],
        "metadata": {"session_id": "bug2a"},
    })
    assert r.status_code == 200
    state = app.state.circuit_tracker.subtasks["bug2a"]
    assert state.opus_attempts == 1   # one call served by Opus
    assert state.opus_failures == 0   # the failure was produced by tactical, not Opus


def test_handoff_after_second_opus_failure_not_first():
    app, client = make_client(FakeUpstream())
    app.state.circuit_tracker.record_served("bug2b", "gemini_tactical")

    def send(tb: str):
        return client.post("/v1/chat/completions", json={
            "model": "opus_apex",
            "messages": [{"role": "user", "content": tb}],
            "metadata": {"session_id": "bug2b"},
        })

    assert send(TB + " #tactical").status_code == 200   # escalation into Opus
    assert send(TB + " #opus-1").status_code == 200     # Opus failed once: keep going
    r = send(TB + " #opus-2")                            # Opus failed twice: hand off
    assert r.status_code == 423
    assert r.json()["detail"]["reason"] == "opus_exhausted_2x"


# ── Bug 3: test guard must judge added lines only and never deadlock the agent ──

def _patch_conversation(old: str, new: str, path: str = "tests/test_api.py") -> list[dict]:
    return [{"role": "user", "content": f"perbaiki {path}"}] + tool_turn(
        "p1", "patch", "patched", {"path": path, "old_string": old, "new_string": new}
    )


def test_removing_skip_marker_is_allowed():
    _, client = make_client(FakeUpstream())
    msgs = _patch_conversation("@pytest.mark.skip\ndef test_a():", "def test_a():")
    r = client.post("/v1/chat/completions", json={"model": "gemini_tactical", "messages": msgs})
    assert r.status_code == 200


def test_adding_skip_rejected_once_then_escalates_with_revert_notice():
    fake = FakeUpstream()
    app, client = make_client(fake)
    msgs = _patch_conversation("def test_a():", "@pytest.mark.skip\ndef test_a():")
    body = {"model": "gemini_tactical", "messages": msgs, "metadata": {"session_id": "bug3"}}

    first = client.post("/v1/chat/completions", json=body)
    assert first.status_code == 403
    assert "TEST_TAMPERING_DETECTED" in first.json()["detail"]
    assert fake.calls == []

    # Same applied diff still in the tail: no second 403 (that would deadlock the agent).
    second = client.post("/v1/chat/completions", json=body)
    assert second.status_code == 200
    assert second.headers["x-routed-model"] == "opus_apex"
    system = [m for m in fake.calls[-1]["messages"] if m["role"] == "system"]
    assert system and "Test Integrity Guard" in system[0]["content"]


def test_patch_on_non_test_file_is_not_inspected():
    _, client = make_client(FakeUpstream())
    # Mentions a test file, but the patch itself only touches source code.
    msgs = [{"role": "user", "content": "lihat tests/test_api.py lalu perbaiki src/api.py"}] + tool_turn(
        "p1", "patch", "patched", {"path": "src/api.py", "old_string": "x = 1", "new_string": "x = items.skip(1)"}
    )
    r = client.post("/v1/chat/completions", json={"model": "gemini_tactical", "messages": msgs})
    assert r.status_code == 200


def test_validate_diff_detects_assertion_removal_in_multiline_patch():
    guard = TestIntegrityGuard()
    msgs = _patch_conversation("def test_a():\n    assert f(1) == 2\n    assert f(2) == 3", "def test_a():\n    pass")
    diff = extract_routing_context({"messages": msgs}).metadata.last_diff
    res = guard.validate_diff(diff)
    assert not res.approved
    assert res.reason == "ASSERTION_COUNT_REDUCED"


def test_validate_diff_handles_git_style_multi_file_diff():
    guard = TestIntegrityGuard()
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n+x.only(1)\n"
        "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "-    assert x\n+    assert x\n"
    )
    assert guard.validate_diff(diff).approved
    tampered = diff.replace("+    assert x\n", "+    pytest.skip('later')\n")
    res = guard.validate_diff(tampered)
    assert not res.approved and res.reason == "TEST_TAMPERING_DETECTED"


# ── Bug 4: upstream errors in stream mode keep their real status ──

def test_stream_passes_through_client_error_status():
    fake = FakeUpstream(lambda model, p: httpx.Response(401, json={"error": {"message": "bad key"}}))
    _, client = make_client(fake)
    r = client.post("/v1/chat/completions", json={
        "model": "gemini_tactical", "stream": True, "messages": [{"role": "user", "content": "hai"}]
    })
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "bad key"
    assert len(fake.calls) == 1   # 401 is not transient: no fallback


def test_upstream_500_triggers_fallback():
    app, _ = make_client(FakeUpstream())
    opus_id = app.state.upstream.resolve_model_id("opus_apex")
    fake = FakeUpstream(lambda model, p: httpx.Response(500) if model == opus_id else _ok(None))
    _, client = make_client(fake)
    for stream in (False, True):
        fake.calls.clear()
        r = client.post("/v1/chat/completions", json={
            "model": "opus_apex", "stream": stream, "messages": [{"role": "user", "content": "hai"}]
        })
        assert r.status_code == 200
        assert r.headers["x-routed-final-alias"] == "sonnet_fallback"


# ── Bug 5: test-file requests must never fall back into Tier 3 ──

def test_fallback_never_lands_on_tier3_for_test_files():
    app, _ = make_client(FakeUpstream())
    tactical_id = app.state.upstream.resolve_model_id("gemini_tactical")
    flash_id = app.state.upstream.resolve_model_id("gemini_executor")
    fake = FakeUpstream(lambda model, p: httpx.Response(503) if model == tactical_id else _ok(None))
    _, client = make_client(fake)
    for stream in (False, True):
        fake.calls.clear()
        r = client.post("/v1/chat/completions", json={
            "model": "auto", "stream": stream, "messages": [{"role": "user", "content": "update tests/test_api.py"}]
        })
        assert r.status_code == 503
        assert flash_id not in fake.models


def test_fallback_to_tier3_still_allowed_for_non_test_files():
    app, _ = make_client(FakeUpstream())
    tactical_id = app.state.upstream.resolve_model_id("gemini_tactical")
    fake = FakeUpstream(lambda model, p: httpx.Response(503) if model == tactical_id else _ok(None))
    _, client = make_client(fake)
    r = client.post("/v1/chat/completions", json={
        "model": "gemini_tactical", "messages": [{"role": "user", "content": "update src/api.py"}]
    })
    assert r.status_code == 200
    assert r.headers["x-routed-final-alias"] == "gemini_executor"
