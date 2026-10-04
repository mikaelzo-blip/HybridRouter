import pytest
from src.router.extractor import extract_routing_context
from src.config import load_router_config
from src.router.engine import RouterEngine, RoutingContext
from src.schemas import RequestMetadata


@pytest.fixture
def router_engine():
    cfg = load_router_config("config/9router-production.yaml")
    return RouterEngine(cfg)


def test_tail_retry_resets_on_success(router_engine):
    """Ancient errors in conversation history must not permanently escalate.
    If the latest tool result is successful, retry_count must be 0 and target must be Gemini.
    """
    messages = [
        {"role": "user", "content": "Tolong perbaiki bug"},
        {"role": "assistant", "content": "Running test...", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "terminal", "arguments": "pytest"}}]},
        {"role": "tool", "content": "FAILED tests/test_app.py - AssertionError: 500 != 200\nTraceback (most recent call last):", "tool_call_id": "c1", "name": "terminal"},
        {"role": "assistant", "content": "Fixing...", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "patch", "arguments": "{}"}}]},
        {"role": "tool", "content": "Error: could not match target block", "tool_call_id": "c2", "name": "patch"},
        {"role": "assistant", "content": "Retrying patch...", "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "tool", "content": "Error: permission denied", "tool_call_id": "c3", "name": "write_file"},
        {"role": "assistant", "content": "Retrying with correct permissions...", "tool_calls": [{"id": "c4", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "tool", "content": "File written successfully.", "tool_call_id": "c4", "name": "write_file"},
        {"role": "assistant", "content": "Re-running test...", "tool_calls": [{"id": "c5", "type": "function", "function": {"name": "terminal", "arguments": "pytest"}}]},
        {"role": "tool", "content": "====== 10 passed in 0.5s ======", "tool_call_id": "c5", "name": "terminal"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert ctx.metadata.retry_count == 0, f"Expected retry_count=0 after success, got {ctx.metadata.retry_count}"

    decision = router_engine.route(ctx)
    assert decision.target_model in ("gemini_executor", "gemini_tactical")
    assert decision.target_model != "opus_apex"


def test_tail_retry_counts_consecutive_tail_failures_only():
    """Only consecutive failures at the immediate tail of the conversation should count as retries."""
    messages = [
        {"role": "user", "content": "Tolong implementasikan modul"},
        {"role": "assistant", "content": "Done", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "tool", "content": "File written.", "tool_call_id": "c1", "name": "write_file"},
        # Now 2 failures in a row at the tail
        {"role": "assistant", "content": "Running test...", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "terminal", "arguments": "pytest"}}]},
        {"role": "tool", "content": "FAILED tests/test_app.py - AssertionError", "tool_call_id": "c2", "name": "terminal"},
        {"role": "assistant", "content": "Trying again...", "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "terminal", "arguments": "pytest"}}]},
        {"role": "tool", "content": "FAILED tests/test_app.py - AssertionError", "tool_call_id": "c3", "name": "terminal"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert ctx.metadata.retry_count == 2


def test_agent_depth_prevents_turn1_trap():
    """When an agent has completed several tool steps, turn must not remain 1."""
    messages = [
        {"role": "user", "content": "Tolong buatkan skema database migration untuk invoice"},
        {"role": "assistant", "content": "Baik saya cek", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "content": "content", "tool_call_id": "c1", "name": "read_file"},
        {"role": "assistant", "content": "Saya buat file", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "tool", "content": "saved", "tool_call_id": "c2", "name": "write_file"},
        {"role": "assistant", "content": "Saya jalankan migrasi", "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "terminal", "arguments": "alembic upgrade head"}}]},
        {"role": "tool", "content": "done", "tool_call_id": "c3", "name": "terminal"},
    ]
    ctx = extract_routing_context({"messages": messages})
    # Agent is already at step 4 (1 user message + 3 assistant tool turns), turn should be > 1
    assert ctx.metadata.turn > 1


def test_quality_gate_routes_to_claude(router_engine):
    """Final quality gate (diff_review + requesting-code-review skill) routes to Claude."""
    messages = [
        {"role": "user", "content": "Validasi dan review kualitas hasil kerja sebelum commit"},
        {
            "role": "assistant",
            "content": "Loading code review skill.",
            "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:requesting-code-review"}'}},
            ],
        },
        {"role": "tool", "content": "Skill loaded.", "tool_call_id": "c0", "name": "skill_view"},
        {
            "role": "assistant",
            "content": "Running diff review.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "diff_review", "arguments": '{"diff": "..."}'}},
            ],
        },
        {"role": "tool", "content": "Diff summary...", "tool_call_id": "c1", "name": "diff_review"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "quality_gate" in ctx.metadata.intent

    decision = router_engine.route(ctx)
    assert decision.target_model in ("sonnet_fallback", "opus_apex")
    assert decision.rule_name == "quality_gate_review"


def test_intermediate_diff_review_routes_to_claude(router_engine):
    """diff_review mid-session routes to Claude (sonnet_fallback) for thorough code review."""
    messages = [
        {"role": "user", "content": "Cek perubahannya"},
        {
            "role": "assistant",
            "content": "Checking diff.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "diff_review", "arguments": '{"diff": "..."}'}},
            ],
        },
        {"role": "tool", "content": "Diff looks ok.", "tool_call_id": "c1", "name": "diff_review"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_code_review" in ctx.metadata.intent

    decision = router_engine.route(ctx)
    # Must route to Claude (sonnet_fallback) for code review
    assert decision.target_model == "sonnet_fallback"
    assert decision.rule_name == "agent_code_review"


def test_opus_rescue_capped_to_max_attempts(router_engine):
    """When opus_attempts >= 2, rescue_opus must NOT be selected; route to gemini_tactical instead."""
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=4, turn=5, opus_attempts=2),
        total_tokens=50000,
        messages=[{"role": "user", "content": "Buntu lagi"}]
    )
    decision = router_engine.route(ctx)
    assert decision.target_model != "opus_apex"
    assert decision.target_model == "gemini_tactical"


def test_gemini_tactical_fallback_does_not_leak_to_claude():
    """config/9router-production.yaml fallback_chain for gemini_tactical must not point to Claude."""
    cfg = load_router_config("config/9router-production.yaml")
    gemini_fallback = cfg.resilience.fallback_chain.get("gemini_tactical")
    assert gemini_fallback != "sonnet_fallback"
    assert gemini_fallback in ("gemini_executor", None)


def test_server_enforces_opus_attempt_limit_across_requests():
    """Server must track opus attempts across requests in session and stop routing to opus after 2 attempts."""
    from fastapi.testclient import TestClient
    from unittest.mock import patch, MagicMock
    from src.server.app import create_app

    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "ok"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    # Repeated high retry payload
    payload = {
        "model": "auto",
        "metadata": {"session_id": "session-test-cap", "retry_count": 4},
        "messages": [{"role": "user", "content": "Tolong selesaikan ini"}]
    }

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "opus_apex")):
        r1 = client.post("/v1/chat/completions", json=payload)
        assert r1.status_code == 200
        assert r1.headers["x-routed-model"] == "opus_apex"

        r2 = client.post("/v1/chat/completions", json=payload)
        assert r2.status_code == 200
        assert r2.headers["x-routed-model"] == "opus_apex"

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_tactical")):
        # 3rd request: Opus quota exceeded (2 attempts), must divert to gemini_tactical
        r3 = client.post("/v1/chat/completions", json=payload)
        assert r3.status_code == 200
        assert r3.headers["x-routed-model"] == "gemini_tactical"

