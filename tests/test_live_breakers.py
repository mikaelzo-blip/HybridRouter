import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from src.server.app import create_app


def test_breakers_status_and_reset():
    app = create_app(None)
    client = TestClient(app)

    # Check status endpoint
    resp = client.get("/breakers/status?session_id=test_sess")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "test_sess"
    assert data["identical_error_count"] == 0
    assert data["empty_diff_count"] == 0
    assert data["should_escalate"] is False

    # Reset endpoint
    reset_resp = client.post("/breakers/reset?session_id=test_sess")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "reset"


def test_live_traceback_loop_forces_escalation():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "ok"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    tb = "Traceback (most recent call last):\n  File 'a.py', line 1\nTypeError: null"
    session_id = "sess_loop_tb"

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_executor")) as mock_forward:
        # Send 1st and 2nd error: normal routing
        for _ in range(2):
            client.post("/v1/chat/completions", json={
                "model": "auto",
                "messages": [{"role": "user", "content": "fix"}],
                "metadata": {"session_id": session_id, "last_traceback": tb, "retry_count": 0}
            })
            assert mock_forward.call_args[0][0] == "gemini_executor"

        # 3rd identical error: loop breaker triggers! Must force escalate above gemini_executor -> gemini_tactical
        client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "fix"}],
            "metadata": {"session_id": session_id, "last_traceback": tb, "retry_count": 0}
        })
        assert mock_forward.call_args[0][0] == "gemini_tactical"


def test_live_empty_diff_forces_escalation():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "ok"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    session_id = "sess_empty_diff"

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_executor")) as mock_forward:
        # Send 2 empty diffs
        for _ in range(2):
            client.post("/v1/chat/completions", json={
                "model": "auto",
                "messages": [{"role": "user", "content": "run"}],
                "metadata": {"session_id": session_id, "last_diff": "", "retry_count": 0}
            })
            assert mock_forward.call_args[0][0] == "gemini_executor"

        # 3rd empty diff: triggers empty diff guard -> escalate to gemini_tactical
        client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "run"}],
            "metadata": {"session_id": session_id, "last_diff": "", "retry_count": 0}
        })
        assert mock_forward.call_args[0][0] == "gemini_tactical"
