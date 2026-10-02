import pytest
from httpx import ASGITransport, AsyncClient, Response
from unittest.mock import AsyncMock, patch, MagicMock
from src.server.app import create_app
from src.config import load_router_config, AppSettings


@pytest.fixture
def app():
    settings = AppSettings(
        port=20250,
        host="127.0.0.1",
        upstream_base_url="http://127.0.0.1:20128/v1",
        routing_config_path="config/9router-production.yaml"
    )
    return create_app(settings)


@pytest.mark.asyncio
async def test_health_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


@pytest.mark.asyncio
async def test_models_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        model_ids = [m["id"] for m in data["data"]]
        assert "auto" in model_ids
        assert "opus_apex" in model_ids
        assert "gemini_tactical" in model_ids
        assert "gemini_executor" in model_ids


@pytest.mark.asyncio
async def test_debug_route_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "Help me fix bug"}],
            "metadata": {
                "retry_count": 4,
                "files_target": ["src/service.py"]
            }
        }
        resp = await client.post("/debug/route", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_rule"] == "rescue_opus"
        assert data["target_alias"] == "opus_apex"
        assert "strip_terminal_noise" in data["applied_transforms"]
        assert "inject_system_prompt" in data["applied_transforms"]


@pytest.mark.asyncio
async def test_chat_completions_forwarding_and_fallback(app):
    transport = ASGITransport(app=app)
    
    # Mock upstream client: first call to opus returns 429, fallback to sonnet returns 200
    mock_resp_429 = Response(status_code=429, json={"error": "Rate limited"})
    mock_resp_200 = Response(
        status_code=200,
        json={
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Sonnet response"}}]
        }
    )
    
    with patch.object(app.state.upstream.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_429, mock_resp_200]
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "auto",
                "messages": [{"role": "user", "content": "Audit file"}],
                "metadata": {"retry_count": 4}
            }
            resp = await client.post("/v1/chat/completions", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["choices"][0]["message"]["content"] == "Sonnet response"
            # Verify fallback was invoked
            assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_auto_routing_without_metadata_routes_to_tactical_on_service_mention(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Standard OpenAI client format (no metadata dict)
        payload = {
            "model": "auto",
            "messages": [
                {"role": "user", "content": "Tolong perbaiki bug pada src/services/payment.py"}
            ]
        }
        resp = await client.post("/debug/route", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_rule"] == "backend_core"
        assert data["target_alias"] == "gemini_tactical"


@pytest.mark.asyncio
async def test_auto_routing_without_metadata_routes_to_opus_on_architecture_prompt(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Standard OpenAI client format (no metadata dict)
        payload = {
            "model": "auto",
            "messages": [
                {"role": "user", "content": "Rancang system_architecture baru dan buat schema.prisma"}
            ]
        }
        resp = await client.post("/debug/route", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_rule"] == "apex_design"
        assert data["target_alias"] == "opus_apex"

