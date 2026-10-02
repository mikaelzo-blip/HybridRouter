from contextlib import asynccontextmanager
import logging
from typing import Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from src.config import AppSettings, load_router_config
from src.router.engine import RouterEngine, RoutingContext
from src.router.extractor import extract_routing_context
from src.schemas import RequestMetadata
from src.server.upstream import UpstreamClient
from src.breakers.circuit import CircuitBreakerTracker
from src.guard.test_integrity import TestIntegrityGuard, is_test_file

logger = logging.getLogger("hybrid_router")

TIER_ESCALATION = {
    "gemini_executor": "gemini_tactical",
    "flash": "gemini_tactical",
    "gemini_tactical": "opus_apex",
    "pro": "opus_apex",
    "opus_apex": "sonnet_fallback",
    "opus": "sonnet_fallback",
}


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
    app.state.circuit_tracker = CircuitBreakerTracker()
    app.state.test_guard = TestIntegrityGuard()

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
        ctx = extract_routing_context(body)
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

    @app.get("/breakers/status")
    async def breakers_status(session_id: str = "default"):
        tracker: CircuitBreakerTracker = app.state.circuit_tracker
        state = tracker._get_or_create_state(session_id)
        should_escalate = (
            state.consecutive_identical_tracebacks >= tracker.identical_traceback_limit
            or state.consecutive_empty_diffs >= tracker.empty_diff_limit
            or state.iterations >= tracker.max_iterations
        )
        return {
            "session_id": session_id,
            "identical_error_count": state.consecutive_identical_tracebacks,
            "empty_diff_count": state.consecutive_empty_diffs,
            "should_escalate": should_escalate,
            "iterations": state.iterations,
        }

    @app.post("/breakers/reset")
    async def breakers_reset(session_id: str = "default"):
        app.state.circuit_tracker.reset_subtask(session_id)
        return {
            "status": "reset",
            "session_id": session_id,
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        model_requested = body.get("model", "auto")
        ctx = extract_routing_context(body)
        metadata = ctx.metadata
        meta_dict = body.get("metadata", {})

        target_alias = model_requested

        if model_requested == "auto":
            decision = router_engine.route(ctx)
            target_alias = decision.target_model
            # Note: transforms can be applied to messages in body if needed
            logger.info("Routing decision: %s -> %s", decision.rule_name, target_alias)

        session_id = meta_dict.get("session_id") or metadata.session_id or metadata.subtask_id or "default"
        tb = meta_dict.get("last_traceback") or metadata.last_traceback
        diff = meta_dict.get("last_diff") if "last_diff" in meta_dict else metadata.last_diff

        if tb is not None or diff is not None:
            breaker_decision = app.state.circuit_tracker.record_failure(
                subtask_id=session_id,
                traceback_text=tb,
                diff_text=diff,
                current_tier=target_alias
            )
            if breaker_decision.action in ("force_escalate_one_tier", "rollback_last_clean_commit_then_escalate_one_tier", "reject_patch_and_escalate"):
                old_target = target_alias
                target_alias = TIER_ESCALATION.get(target_alias, target_alias)
                logger.warning(
                    "Circuit breaker triggered (%s) for session %s. Escalated %s -> %s",
                    breaker_decision.reason, session_id, old_target, target_alias
                )

        # Check test integrity guard for Tier 3
        if target_alias in ("gemini_executor", "flash"):
            for f in metadata.files_target:
                if is_test_file(f):
                    logger.warning("Tier 3 attempt to modify test file rejected: %s", f)
                    raise HTTPException(
                        status_code=403,
                        detail=f"Tier 3 ({target_alias}) is prohibited from modifying test files. Escalation required."
                    )

        is_stream = bool(body.get("stream", False))

        if is_stream:
            try:
                stream_gen, final_alias, headers = await upstream.forward_stream(target_alias, body)
                return StreamingResponse(
                    stream_gen,
                    status_code=200,
                    headers={"Content-Type": headers.get("content-type", "text/event-stream")}
                )
            except Exception as e:
                logger.error("Upstream stream error on alias %s: %s", target_alias, e)
                raise HTTPException(status_code=502, detail=f"Upstream provider failure: {str(e)}")

        try:
            resp, final_alias = await upstream.forward_request(target_alias, body)
        except Exception as e:
            logger.error("Upstream error on alias %s: %s", target_alias, e)
            raise HTTPException(status_code=502, detail=f"Upstream provider failure: {str(e)}")

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={"Content-Type": resp.headers.get("content-type", "application/json")}
        )

    return app
