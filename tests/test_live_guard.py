import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from src.server.app import create_app


def test_reject_tier3_touching_test_files():
    app = create_app(None)
    client = TestClient(app)

    # gemini_executor attempting to touch a test file
    resp = client.post("/v1/chat/completions", json={
        "model": "gemini_executor",
        "messages": [{"role": "user", "content": "update tests"}],
        "metadata": {
            "session_id": "test_guard_sess",
            "files_target": ["tests/test_something.py"]
        }
    })
    assert resp.status_code == 403
    assert "prohibited from modifying test files" in resp.json()["detail"].lower()


def test_allow_tier2_pro_touching_test_files():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "ok"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_tactical")) as mock_forward:
        resp = client.post("/v1/chat/completions", json={
            "model": "gemini_tactical",
            "messages": [{"role": "user", "content": "update tests"}],
            "metadata": {
                "session_id": "test_guard_sess",
                "files_target": ["tests/test_something.py"]
            }
        })
        assert resp.status_code == 200
        assert mock_forward.call_count == 1


def test_allow_tier3_touching_non_test_files():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "ok"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_executor")) as mock_forward:
        resp = client.post("/v1/chat/completions", json={
            "model": "gemini_executor",
            "messages": [{"role": "user", "content": "update code"}],
            "metadata": {
                "session_id": "test_guard_sess",
                "files_target": ["src/service.py"]
            }
        })
        assert resp.status_code == 200
        assert mock_forward.call_count == 1
