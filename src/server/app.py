from contextlib import asynccontextmanager
import logging
from typing import Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from src.config import AppSettings, load_router_config
from src.router.engine import RouterEngine, RoutingContext
from src.schemas import RequestMetadata
from src.server.upstream import UpstreamClient

logger = logging.getLogger("hybrid_router")


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or AppSettings()
    config = load_router_config(app_settings.routing_config_path)
    router_engine = RouterEngine(config)
    upstream = UpstreamClient(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await upstream.close()

    app = FastAPI(title="Hybrid Autorouter 3-Tier", version=config.version, lifespan=lifespan)
    app.state.upstream = upstream
    app.state.router_engine = router_engine

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": config.version,
            "port": app_settings.port,
            "upstream": upstream.base_url
        }

    @app.get("/v1/models")
    async def list_models():
        model_list = [
            {"id": "auto", "object": "model", "owned_by": "hybrid-router"},
            {"id": "opus_apex", "object": "model", "owned_by": "hybrid-router"},
            {"id": "gemini_tactical", "object": "model", "owned_by": "hybrid-router"},
            {"id": "gemini_executor", "object": "model", "owned_by": "hybrid-router"},
            {"id": "sonnet_fallback", "object": "model", "owned_by": "hybrid-router"},
        ]
        # Include resolved model IDs from config
        for alias, mcfg in config.models.items():
            if mcfg.model and mcfg.model not in [m["id"] for m in model_list]:
                model_list.append({
                    "id": mcfg.model,
                    "object": "model",
                    "owned_by": mcfg.provider
                })
        return {"object": "list", "data": model_list}

    @app.post("/debug/route")
    async def debug_route(request: Request):
        body = await request.json()
        messages = body.get("messages", [])
        meta_dict = body.get("metadata", {})
        metadata = RequestMetadata(**meta_dict)

        ctx = RoutingContext(
            metadata=metadata,
            total_tokens=body.get("total_tokens", 0),
            messages=messages,
            request=body
        )
        decision = router_engine.route(ctx)
        resolved_model = upstream.resolve_model_id(decision.target_model)

        return {
            "matched_rule": decision.rule_name,
            "target_alias": decision.target_model,
            "resolved_upstream_model": resolved_model,
            "applied_transforms": decision.applied_transforms,
            "escalation_reason": decision.escalation_reason,
            "pipeline": decision.pipeline
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        model_requested = body.get("model", "auto")
        messages = body.get("messages", [])
        meta_dict = body.get("metadata", {})
        metadata = RequestMetadata(**meta_dict)

        target_alias = model_requested

        if model_requested == "auto":
            ctx = RoutingContext(
                metadata=metadata,
                total_tokens=body.get("total_tokens", 0),
                messages=messages,
                request=body
            )
            decision = router_engine.route(ctx)
            target_alias = decision.target_model
            # Note: transforms can be applied to messages in body if needed
            logger.info("Routing decision: %s -> %s", decision.rule_name, target_alias)

        is_stream = bool(body.get("stream", False))

        try:
            resp, final_alias = await upstream.forward_request(target_alias, body)
        except Exception as e:
            logger.error("Upstream error on alias %s: %s", target_alias, e)
            raise HTTPException(status_code=502, detail=f"Upstream provider failure: {str(e)}")

        if is_stream:
            return StreamingResponse(
                resp.aiter_bytes(),
                status_code=resp.status_code,
                headers=dict(resp.headers)
            )

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={"Content-Type": resp.headers.get("content-type", "application/json")}
        )

    return app
