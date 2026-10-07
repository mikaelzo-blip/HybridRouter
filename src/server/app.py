from contextlib import asynccontextmanager
import copy
import hashlib
import json
import logging
from typing import Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from src.config import AppSettings, load_router_config
from src.router.engine import RouterEngine, RoutingContext
from src.router.extractor import extract_routing_context
from src.schemas import RequestMetadata
from src.server.upstream import UpstreamClient, UpstreamStatusError
from src.router.transforms import inject_system_prompt
from src.breakers.circuit import CircuitBreakerTracker
from src.guard.test_integrity import TestIntegrityGuard, is_test_file
from src.observability.spend import TokenSpendTracker

logger = logging.getLogger("hybrid_router")

TIER_ESCALATION = {
    "gemini_executor": "gemini_tactical",
    "flash": "gemini_tactical",
    "gemini_tactical": "opus_apex",
    "pro": "opus_apex",
    "opus_apex": "sonnet_fallback",
    "opus": "sonnet_fallback",
}

TIER3_ALIASES = frozenset({"gemini_executor", "flash"})


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
    app.state.circuit_tracker = CircuitBreakerTracker(state_file=app_settings.circuit_state_file)
    app.state.test_guard = TestIntegrityGuard()
    app.state.spend_tracker = TokenSpendTracker(state_file=app_settings.spend_state_file)

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

    @app.get("/observability/spend")
    async def observability_spend(subtask_id: str | None = None):
        spend_tracker: TokenSpendTracker = app.state.spend_tracker
        if subtask_id:
            spend = spend_tracker.get_subtask_spend(subtask_id)
            is_anomaly, reason = spend_tracker.check_anomaly(subtask_id)
            spend["is_anomaly"] = is_anomaly
            spend["anomaly_reason"] = reason
            return spend
        return spend_tracker.get_daily_summary()

    @app.post("/guard/validate_patch")
    async def validate_patch(request: Request):
        payload = await request.json()
        tier = payload.get("tier", "gemini_executor")
        file_path = payload.get("file_path", "")
        original_content = payload.get("original_content", "")
        new_content = payload.get("new_content", "")
        result = app.state.test_guard.validate_patch(tier, file_path, original_content, new_content)
        return result.model_dump()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        model_requested = body.get("model", "auto")
        ctx = extract_routing_context(body)
        metadata = ctx.metadata
        meta_dict = body.get("metadata", {})
        header_sess = request.headers.get("x-session-id")
        session_id = meta_dict.get("session_id") or metadata.session_id or metadata.subtask_id or header_sess or "default"
        state = app.state.circuit_tracker._get_or_create_state(session_id)
        metadata.opus_attempts = max(metadata.opus_attempts, state.opus_attempts)

        target_alias = model_requested
        rule_name = "direct_request"

        # Build reverse lookup: upstream model ID → alias (e.g. "ag/claude-sonnet-4-6" → "sonnet_fallback")
        model_id_to_alias: dict[str, str] = {
            mcfg.model: alias
            for alias, mcfg in config.models.items()
            if mcfg.model
        }

        # Normalize: if client sent a raw upstream model ID, remap to known alias
        normalized = model_id_to_alias.get(model_requested)
        if normalized:
            logger.info("Normalized model ID %s → alias %s", model_requested, normalized)
            model_requested = normalized

        # Route: "auto", after normalization from raw model ID, or completely unknown model
        decision = None
        should_route = (model_requested == "auto") or bool(normalized) or (model_requested not in config.models)
        if should_route:
            decision = router_engine.route(ctx)
            target_alias = decision.target_model
            rule_name = decision.rule_name
            logger.info("Routing decision: %s -> %s", decision.rule_name, target_alias)
        else:
            # Known alias passed directly — respect it
            target_alias = model_requested

        # Prepare outbound payload: apply transformed messages if produced by router transforms
        outbound_body = copy.deepcopy(body)
        if decision and decision.transformed_messages:
            outbound_body["messages"] = decision.transformed_messages

        tb = meta_dict.get("last_traceback") or metadata.last_traceback
        diff = meta_dict.get("last_diff") if "last_diff" in meta_dict else metadata.last_diff

        guard_note: str | None = None
        if diff:
            # Inspect only the lines the diff adds to test files (removing a skip is fine).
            res = app.state.test_guard.validate_diff(diff, fallback_paths=metadata.files_target)
            if not res.approved:
                diff_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]
                if app.state.circuit_tracker.mark_diff_rejected(session_id, diff_hash):
                    # First sighting: reject the patch.
                    logger.warning("TestIntegrityGuard rejected diff: %s (%s)", res.reason, res.details)
                    raise HTTPException(
                        status_code=403,
                        detail=f"Test integrity violation ({res.reason}): {res.details}"
                    )
                # The same (already applied) diff is still in the conversation tail.
                # Rejecting again would deadlock the agent, so escalate one tier and tell
                # the model to revert it instead.
                old_target = target_alias
                target_alias = TIER_ESCALATION.get(target_alias, target_alias)
                guard_note = (
                    f"PERINGATAN Test Integrity Guard: patch terakhir ditolak ({res.reason}: {res.details}). "
                    "Kembalikan perubahan tersebut. Jangan melewati, menonaktifkan, atau mengurangi assertion pada test."
                )
                logger.warning(
                    "Rejected diff still present for session %s. Escalated %s -> %s and injected revert notice.",
                    session_id, old_target, target_alias
                )

        if tb is not None or diff is not None:
            breaker_decision = app.state.circuit_tracker.record_failure(
                subtask_id=session_id,
                traceback_text=tb,
                diff_text=diff,
                current_tier=target_alias
            )
            if breaker_decision.action == "human_handoff":
                logger.error("Human handoff triggered for session %s: %s", session_id, breaker_decision.handoff_summary)
                raise HTTPException(
                    status_code=423,
                    detail={
                        "error": "human_handoff_required",
                        "reason": breaker_decision.reason,
                        "summary": breaker_decision.handoff_summary
                    }
                )
            if breaker_decision.action in ("force_escalate_one_tier", "rollback_last_clean_commit_then_escalate_one_tier", "reject_patch_and_escalate"):
                old_target = target_alias
                target_alias = TIER_ESCALATION.get(target_alias, target_alias)
                logger.warning(
                    "Circuit breaker triggered (%s) for session %s. Escalated %s -> %s",
                    breaker_decision.reason, session_id, old_target, target_alias
                )
        if decision is not None:
            sticky_alias = app.state.circuit_tracker.apply_sticky(session_id, target_alias)
            if sticky_alias != target_alias:
                logger.info("Sticky tier: %s -> %s for session %s", target_alias, sticky_alias, session_id)
                target_alias = sticky_alias

        if not tb and (diff is None or diff.strip()) and metadata.retry_count == 0:
            app.state.circuit_tracker.record_success(subtask_id=session_id)

        # Check test integrity guard for Tier 3
        if target_alias in ("gemini_executor", "flash"):
            for f in metadata.files_target:
                if is_test_file(f):
                    if model_requested == "auto":
                        old_target = target_alias
                        target_alias = TIER_ESCALATION.get(target_alias, "gemini_tactical")
                        logger.warning(
                            "Tier 3 auto-routed request touches test file %s. Auto-escalating %s -> %s as per test integrity policy.",
                            f, old_target, target_alias
                        )
                        break
                    else:
                        # Direct request to Tier 3: reject ONLY if this is a modification/write action
                        last_user_content = ""
                        for m in reversed(ctx.messages):
                            if m.get("role") == "user":
                                c = m.get("content")
                                if isinstance(c, str):
                                    last_user_content = c
                                elif isinstance(c, list):
                                    last_user_content = " ".join(part.get("text", "") for part in c if isinstance(part, dict))
                                break

                        mod_keywords = ("update", "edit", "fix", "perbaiki", "ubah", "modify", "write", "tulis", "patch", "refactor", "hapus", "delete", "create", "buat")
                        is_explicit_target = bool(meta_dict.get("files_target"))
                        has_mod_intent = any(kw in last_user_content.lower() for kw in mod_keywords) or ("agent_activity_coding" in metadata.intent)

                        if is_explicit_target or has_mod_intent:
                            logger.warning("Tier 3 attempt to modify test file rejected: %s", f)
                            raise HTTPException(
                                status_code=403,
                                detail=f"Tier 3 ({target_alias}) is prohibited from modifying test files. Escalation required."
                            )

        if guard_note:
            outbound_body["messages"] = inject_system_prompt(outbound_body.get("messages", []), guard_note)

        # Requests touching test files must never fall back into Tier 3 (read-only for tests).
        blocked_aliases: frozenset[str] = frozenset()
        if target_alias not in TIER3_ALIASES and any(is_test_file(f) for f in metadata.files_target):
            blocked_aliases = TIER3_ALIASES

        is_stream = bool(body.get("stream", False))

        if is_stream:
            try:
                stream_gen, final_alias, headers = await upstream.forward_stream(
                    target_alias, outbound_body, blocked_aliases=blocked_aliases
                )
                app.state.circuit_tracker.record_served(session_id, final_alias)
                resp_headers = {
                    "Content-Type": headers.get("content-type", "text/event-stream"),
                    "x-routed-model": target_alias,
                    "x-routed-final-alias": final_alias,
                    "x-routed-rule": rule_name,
                }

                subtask_id = metadata.subtask_id or metadata.session_id or "default"
                async def tracking_stream_generator():
                    prompt_toks = ctx.total_tokens or (len(str(outbound_body.get("messages", []))) // 4)
                    completion_chunks_count = 0
                    usage_captured = False
                    async for chunk in stream_gen:
                        if b'"usage"' in chunk:
                            try:
                                for line in chunk.split(b"\n"):
                                    if line.startswith(b"data: ") and not line.startswith(b"data: [DONE]"):
                                        data = json.loads(line[6:])
                                        u = data.get("usage")
                                        if u and (u.get("prompt_tokens") or u.get("completion_tokens")):
                                            p_tok = u.get("prompt_tokens", 0)
                                            c_tok = u.get("completion_tokens", 0)
                                            app.state.spend_tracker.record_usage(subtask_id, p_tok, c_tok, final_alias)
                                            usage_captured = True
                                            break
                            except Exception:
                                pass
                        completion_chunks_count += 1
                        yield chunk

                    if not usage_captured:
                        est_completion = max(10, completion_chunks_count * 3)
                        app.state.spend_tracker.record_usage(subtask_id, prompt_toks, est_completion, final_alias)

                return StreamingResponse(
                    tracking_stream_generator(),
                    status_code=200,
                    headers=resp_headers
                )
            except UpstreamStatusError as e:
                # Pass the upstream error through with its real status instead of a 200 stream.
                logger.error("Upstream stream returned %d on alias %s", e.status_code, e.alias)
                return Response(
                    content=e.body,
                    status_code=e.status_code,
                    headers={
                        "Content-Type": e.content_type,
                        "x-routed-model": target_alias,
                        "x-routed-final-alias": e.alias,
                        "x-routed-rule": rule_name,
                    },
                )
            except Exception as e:
                logger.error("Upstream stream error on alias %s: %s", target_alias, e)
                raise HTTPException(status_code=502, detail=f"Upstream provider failure: {str(e)}")

        try:
            resp, final_alias = await upstream.forward_request(
                target_alias, outbound_body, blocked_aliases=blocked_aliases
            )
            if resp.status_code == 200:
                app.state.circuit_tracker.record_served(session_id, final_alias)
        except Exception as e:
            logger.error("Upstream error on alias %s: %s", target_alias, e)
            raise HTTPException(status_code=502, detail=f"Upstream provider failure: {str(e)}")

        resp_headers = {
            "Content-Type": resp.headers.get("content-type", "application/json"),
            "x-routed-model": target_alias,
            "x-routed-final-alias": final_alias,
            "x-routed-rule": rule_name,
        }

        # Observability & token spend anomaly tracking
        subtask_id = metadata.subtask_id or metadata.session_id or "default"
        try:
            resp_data = json.loads(resp.content)
            usage = resp_data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if prompt_tokens or completion_tokens:
                app.state.spend_tracker.record_usage(
                    subtask_id, prompt_tokens, completion_tokens, target_alias
                )
                is_anomaly, reason = app.state.spend_tracker.check_anomaly(subtask_id)
                if is_anomaly:
                    resp_headers["x-spend-anomaly"] = "true"
                    resp_headers["x-spend-anomaly-reason"] = reason
        except Exception:
            pass

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers
        )

    return app
