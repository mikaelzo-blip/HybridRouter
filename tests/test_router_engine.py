import pytest
from src.config import load_router_config
from src.schemas import RequestMetadata
from src.router.engine import RouterEngine, RoutingContext
from src.router.transforms import apply_transforms, strip_terminal_noise, inject_system_prompt


@pytest.fixture
def router_engine():
    cfg = load_router_config("config/9router-production.yaml")
    return RouterEngine(cfg)


def test_escalation_ladder_rescue_opus(router_engine):
    # retry_count >= 4 must route to opus_apex with priority 100
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=4, turn=3, files_target=["src/services/auth.py"]),
        total_tokens=5000,
        messages=[{"role": "user", "content": "Fix bug"}]
    )
    decision = router_engine.route(ctx)
    assert decision.rule_name == "rescue_opus"
    assert decision.target_model == "opus_apex"
    assert "strip_terminal_noise" in decision.applied_transforms
    assert "inject_system_prompt" in decision.applied_transforms


def test_escalation_ladder_l1_pro_patch(router_engine):
    # retry_count == 3 must route to gemini_tactical with priority 95
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=3, turn=2, files_target=["src/services/auth.py"]),
        total_tokens=5000,
        messages=[{"role": "user", "content": "Fix logic bug"}]
    )
    decision = router_engine.route(ctx)
    assert decision.rule_name == "l1_pro_patch"
    assert decision.target_model == "gemini_tactical"
    assert "strip_terminal_noise" in decision.applied_transforms


def test_escalation_beats_backend_core_rule(router_engine):
    # Even though file matches src/services/**, retry_count=4 must pick rescue_opus, NOT backend_core
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=4, turn=5, files_target=["src/services/order.ts"]),
        total_tokens=2000,
        messages=[{"role": "user", "content": "Failed again"}]
    )
    decision = router_engine.route(ctx)
    assert decision.rule_name == "rescue_opus"
    assert decision.target_model == "opus_apex"


def test_backend_core_matches_when_retry_count_is_low(router_engine):
    # When retry_count is 0 and target is backend core, backend_core rule (priority 70) triggers
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=0, turn=2, files_target=["src/services/order.ts"]),
        total_tokens=2000,
        messages=[{"role": "user", "content": "Implement service"}]
    )
    decision = router_engine.route(ctx)
    assert decision.rule_name == "backend_core"
    assert decision.target_model == "gemini_tactical"


def test_default_worker_fallback(router_engine):
    # Default non-backend, low retry
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=0, turn=2, files_target=["frontend/Button.tsx"]),
        total_tokens=1500,
        messages=[{"role": "user", "content": "Update button style"}]
    )
    decision = router_engine.route(ctx)
    assert decision.rule_name == "default_worker"
    assert decision.target_model == "gemini_executor"


def test_ingest_then_design_for_large_repo_with_schema(router_engine):
    # turn=1, total_tokens > 40k, schema file -> pipeline [distill, design]
    ctx = RoutingContext(
        metadata=RequestMetadata(retry_count=0, turn=1, files_target=["schema.prisma"]),
        total_tokens=45000,
        messages=[{"role": "user", "content": "Design architecture"}]
    )
    decision = router_engine.route(ctx)
    assert decision.rule_name == "ingest_then_design"
    assert decision.pipeline is not None
    assert len(decision.pipeline) == 2
    assert decision.pipeline[0].target == "gemini_tactical"
    assert decision.pipeline[0].task == "distill"
    assert decision.pipeline[1].target == "opus_apex"
    assert decision.pipeline[1].task == "design"


def test_strip_terminal_noise_keeps_last_n_lines():
    long_terminal = "\n".join(f"Line {i}" for i in range(1, 101))
    cleaned = strip_terminal_noise(long_terminal, keep_last_lines=40)
    lines = cleaned.strip().split("\n")
    assert len(lines) == 40
    assert lines[0] == "Line 61"
    assert lines[-1] == "Line 100"


def test_inject_system_prompt():
    messages = [
        {"role": "system", "content": "Base prompt."},
        {"role": "user", "content": "Do work"}
    ]
    transformed = inject_system_prompt(messages, "Audit arsitektural.")
    assert "Base prompt." in transformed[0]["content"]
    assert "Audit arsitektural." in transformed[0]["content"]
