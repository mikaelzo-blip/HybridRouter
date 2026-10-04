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


def test_auto_mode_escalates_to_pro_when_touching_test_files():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "pro response"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_tactical")) as mock_forward:
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "Tolong perbaiki tests/test_order.py"}]
        })
        assert resp.status_code == 200
        assert resp.headers["x-routed-model"] == "gemini_tactical"
        assert resp.headers["x-routed-rule"] == "test_authoring_and_repair"
        assert mock_forward.call_count == 1


def test_auto_mode_does_not_falsely_flag_history_test_files():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "flash response"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_executor")) as mock_forward:
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [
                {"role": "user", "content": "Jalankan tests/test_order.py"},
                {"role": "assistant", "content": "Tests passed."},
                {"role": "user", "content": "lanjutkan 1 sampai 3"}
            ]
        })
        assert resp.status_code == 200
        assert resp.headers["x-routed-model"] == "gemini_executor"
        assert resp.headers["x-routed-rule"] == "default_worker"
        assert mock_forward.call_count == 1


def test_tier3_allowed_to_run_pytest_without_403():
    app = create_app(None)
    client = TestClient(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices": [{"message": {"content": "tests passed"}}]}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(app.state.upstream, "forward_request", return_value=(mock_resp, "gemini_executor")) as mock_forward:
        resp = client.post("/v1/chat/completions", json={
            "model": "gemini_executor",
            "messages": [{"role": "user", "content": "Tolong jalankan tests/test_login.py untuk cek status"}]
        })
        assert resp.status_code == 200
        assert mock_forward.call_count == 1


def test_opus_quota_not_consumed_on_upstream_failure():
    app = create_app(None)
    client = TestClient(app)

    with patch.object(app.state.upstream, "forward_request", side_effect=Exception("Connection refused")):
        resp = client.post("/v1/chat/completions", json={
            "model": "opus_apex",
            "metadata": {"session_id": "opus_quota_sess"},
            "messages": [{"role": "user", "content": "Bantu arsitektur"}]
        })
        assert resp.status_code == 502

    state = app.state.circuit_tracker._get_or_create_state("opus_quota_sess")
    assert state.opus_attempts == 0
