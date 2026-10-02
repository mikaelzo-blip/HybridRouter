import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from src.schemas import FullRouterConfigFile, ModelConfig, ProviderConfig, ResilienceConfig
from src.server.upstream import UpstreamClient
from src.server.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def mock_config():
    return FullRouterConfigFile(
        version="2026.1",
        providers={
            "antigravity": ProviderConfig(
                base_url="http://127.0.0.1:20128/v1",
                api_key="sk-test-key"
            )
        },
        models={
            "opus_apex": ModelConfig(provider="antigravity", model="ag/claude-opus-4-6-thinking"),
            "sonnet_fallback": ModelConfig(provider="antigravity", model="ag/claude-sonnet-4-6"),
            "gemini_tactical": ModelConfig(provider="antigravity", model="ag/gemini-3.1-pro-low"),
            "gemini_executor": ModelConfig(provider="antigravity", model="ag/gemini-3.8-flash")
        },
        resilience=ResilienceConfig(
            fallback_chain={
                "opus_apex": "sonnet_fallback",
                "gemini_tactical": "sonnet_fallback",
                "gemini_executor": "gemini_tactical"
            }
        )
    )


@pytest.mark.asyncio
async def test_forward_stream_yields_chunks(mock_config):
    upstream = UpstreamClient(mock_config)

    # Mock stream response
    chunks = [
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
        b'data: [DONE]\n\n'
    ]

    async def mock_aiter_bytes():
        for c in chunks:
            yield c

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    mock_resp.aiter_bytes = mock_aiter_bytes

    class MockStreamContext:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch.object(upstream.client, "stream", return_value=MockStreamContext()):
        collected = []
        gen, final_alias, headers = await upstream.forward_stream(
            "gemini_executor",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        async for chunk in gen:
            collected.append(chunk)

        assert final_alias == "gemini_executor"
        assert headers.get("content-type") == "text/event-stream"
        assert b"".join(collected) == b"".join(chunks)

    await upstream.close()


@pytest.mark.asyncio
async def test_forward_stream_pre_stream_fallback(mock_config):
    upstream = UpstreamClient(mock_config)

    # Primary 429 response
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"content-type": "application/json"}

    # Fallback 200 response
    chunks = [b'data: {"choices": [{"delta": {"content": "Fallback"}}]}\n\n']
    async def mock_aiter_bytes():
        for c in chunks:
            yield c

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.headers = {"content-type": "text/event-stream"}
    mock_200.aiter_bytes = mock_aiter_bytes

    class MockContext429:
        async def __aenter__(self):
            return mock_429
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockContext200:
        async def __aenter__(self):
            return mock_200
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    calls = [MockContext429(), MockContext200()]

    with patch.object(upstream.client, "stream", side_effect=calls):
        gen, final_alias, headers = await upstream.forward_stream(
            "gemini_executor",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        collected = []
        async for chunk in gen:
            collected.append(chunk)

        assert final_alias == "gemini_tactical"  # fallback from executor
        assert b"".join(collected) == chunks[0]

    await upstream.close()


def test_server_streaming_endpoint(mock_config):
    app = create_app(None)
    client = TestClient(app)

    chunks = [
        b'data: {"choices": [{"delta": {"content": "Streamed"}}]}\n\n',
        b'data: [DONE]\n\n'
    ]
    async def mock_aiter_bytes():
        for c in chunks:
            yield c

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/event-stream"}
    mock_resp.aiter_bytes = mock_aiter_bytes

    class MockStreamContext:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch.object(app.state.upstream.client, "stream", return_value=MockStreamContext()):
        response = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert b"".join(chunks) in response.content
