from typing import Any, Literal
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str = ""


class ThinkingConfig(BaseModel):
    type: str = "enabled"
    budget_tokens: int = 2048


class ModelParameters(BaseModel):
    max_tokens: int | None = None
    thinking: ThinkingConfig | None = None
    temperature: float | None = None


class ModelConfig(BaseModel):
    provider: str
    model: str
    concurrency_limit: int | None = None
    rpm_limit: str | int | None = None
    tpm_limit: str | int | None = None
    timeout_seconds: int = 60
    parameters: ModelParameters = Field(default_factory=ModelParameters)


class ResilienceConfig(BaseModel):
    on_429: dict[str, Any] = Field(default_factory=dict)
    fallback_chain: dict[str, str] = Field(default_factory=dict)


class ContextTransform(BaseModel):
    action: str
    keep_last_lines: int | None = None
    content: str | None = None


class PipelineStep(BaseModel):
    target: str
    task: str | None = None
    thinking: dict[str, Any] | None = None


class RouterRule(BaseModel):
    name: str
    priority: int
    condition: str
    target: str | None = None
    pipeline: list[PipelineStep] | None = None
    max_attempts_per_subtask: int | None = None
    context_transforms: list[ContextTransform] = Field(default_factory=list)


class RouterConfig(BaseModel):
    routing_strategy: str = "rule_based"
    rules: list[RouterRule] = Field(default_factory=list)


# Actions the proxy knows how to carry out when a breaker trips.
# "rollback_last_clean_commit_then_escalate_one_tier" escalates in the proxy; the
# rollback itself is the client's job (the proxy has no access to the workspace).
BreakerAction = Literal[
    "force_escalate_one_tier",
    "rollback_last_clean_commit_then_escalate_one_tier",
    "human_handoff",
]


class IdenticalErrorLoopBreaker(BaseModel):
    signature: Literal["normalized_traceback"] = "normalized_traceback"
    consecutive: int = Field(default=3, ge=1)
    action: BreakerAction = "force_escalate_one_tier"


class DiffOscillationBreaker(BaseModel):
    # A-B-A detection looks back this many diffs (minimum 3 to fit A-B-A).
    window: int = Field(default=6, ge=3)
    action: BreakerAction = "force_escalate_one_tier"


class EmptyDiffGuard(BaseModel):
    consecutive_empty_diffs: int = Field(default=3, ge=1)
    action: BreakerAction = "rollback_last_clean_commit_then_escalate_one_tier"


class SubtaskLimitsBreaker(BaseModel):
    max_iterations: int = Field(default=12, ge=1)
    max_wall_clock_minutes: int = Field(default=30, ge=1)
    action: BreakerAction = "force_escalate_one_tier"


class OpusExhaustedBreaker(BaseModel):
    max_attempts: int = Field(default=2, ge=1)
    action: BreakerAction = "human_handoff"


class TestTamperingBreaker(BaseModel):
    # Enforced by the test integrity guard (403 once, then escalate). Only this
    # behaviour is implemented, so other values are rejected at load time.
    action: Literal["reject_patch_and_escalate"] = "reject_patch_and_escalate"


class CircuitBreakersConfig(BaseModel):
    identical_error_loop: IdenticalErrorLoopBreaker = Field(default_factory=IdenticalErrorLoopBreaker)
    diff_oscillation: DiffOscillationBreaker = Field(default_factory=DiffOscillationBreaker)
    empty_diff_guard: EmptyDiffGuard = Field(default_factory=EmptyDiffGuard)
    subtask_limits: SubtaskLimitsBreaker = Field(default_factory=SubtaskLimitsBreaker)
    opus_exhausted: OpusExhaustedBreaker = Field(default_factory=OpusExhaustedBreaker)
    test_tampering: TestTamperingBreaker = Field(default_factory=TestTamperingBreaker)


class FullRouterConfigFile(BaseModel):
    version: str = "2026.1"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    resilience: ResilienceConfig = Field(default_factory=ResilienceConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    circuit_breakers: CircuitBreakersConfig = Field(default_factory=CircuitBreakersConfig)


class RequestMetadata(BaseModel):
    model_config = {"extra": "allow"}

    turn: int = 1
    retry_count: int = 0
    files_target: list[str] = Field(default_factory=list)
    intent: list[str] = Field(default_factory=list)
    subtask_id: str | None = None
    session_id: str | None = None
    last_traceback: str | None = None
    last_diff: str | None = None
    opus_attempts: int = 0


class RouteDecision(BaseModel):
    rule_name: str
    target_model: str
    pipeline: list[PipelineStep] | None = None
    applied_transforms: list[str] = Field(default_factory=list)
    transformed_messages: list[dict[str, Any]] | None = None
    escalation_reason: str | None = None
