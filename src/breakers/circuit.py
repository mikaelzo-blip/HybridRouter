import hashlib
import json
from pathlib import Path
import time
from typing import Literal
from pydantic import BaseModel, Field
from src.breakers.normalizer import normalize_traceback


class BreakerEvent(BaseModel):
    subtask_id: str
    traceback_text: str
    diff_text: str
    current_tier: str


class BreakerDecision(BaseModel):
    action: Literal[
        "continue",
        "force_escalate_one_tier",
        "rollback_last_clean_commit_then_escalate_one_tier",
        "human_handoff",
        "reject_patch_and_escalate"
    ]
    reason: str | None = None
    opus_attempts: int = 0
    handoff_summary: str | None = None


class SubtaskCircuitState(BaseModel):
    subtask_id: str
    iterations: int = 0
    start_time: float = Field(default_factory=time.time)
    last_activity: float = Field(default_factory=time.time)
    consecutive_empty_diffs: int = 0
    diff_hash_history: list[str] = Field(default_factory=list)
    last_normalized_traceback: str | None = None
    consecutive_identical_tracebacks: int = 0
    opus_attempts: int = 0
    last_clean_commit: str | None = None
    sticky_left: int = 0


class CircuitBreakerTracker:
    def __init__(
        self,
        max_iterations: int = 12,
        max_wall_clock_minutes: int = 30,
        identical_traceback_limit: int = 3,
        empty_diff_limit: int = 3,
        oscillation_window: int = 6,
        opus_max_attempts: int = 2,
        state_file: str | Path | None = None,
        sticky_turns: int = 5
    ):
        self.max_iterations = max_iterations
        self.max_wall_clock_seconds = max_wall_clock_minutes * 60
        self.identical_traceback_limit = identical_traceback_limit
        self.empty_diff_limit = empty_diff_limit
        self.oscillation_window = oscillation_window
        self.opus_max_attempts = opus_max_attempts
        self.sticky_turns = sticky_turns
        self.state_file = Path(state_file) if state_file else None
        self.subtasks: dict[str, SubtaskCircuitState] = {}
        self.subtask_start_times: dict[str, float] = {}

        if self.state_file and self.state_file.is_file():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for sid, sdata in data.get("subtasks", {}).items():
                    self.subtasks[sid] = SubtaskCircuitState.model_validate(sdata)
            except Exception:
                pass

    def _save_state(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "subtasks": {sid: s.model_dump() for sid, s in self.subtasks.items()}
            }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass

    def record_success(self, subtask_id: str) -> None:
        state = self._get_or_create_state(subtask_id)
        state.consecutive_identical_tracebacks = 0
        state.consecutive_empty_diffs = 0
        state.last_normalized_traceback = None
        # Healthy turn: restart the failure budget and the wall-clock window.
        # Otherwise a long-lived shared session ("default") trips wall_clock/max_iterations forever.
        state.iterations = 0
        state.start_time = time.time()
        self._save_state()

    def _get_or_create_state(self, subtask_id: str) -> SubtaskCircuitState:
        now = time.time()
        # Clean stale sessions if subtasks dict grows large
        if len(self.subtasks) > 200:
            stale_keys = [
                k for k, v in self.subtasks.items()
                if (now - v.last_activity) > self.max_wall_clock_seconds
            ]
            for k in stale_keys:
                self.subtasks.pop(k, None)

        if subtask_id not in self.subtasks:
            state = SubtaskCircuitState(subtask_id=subtask_id, start_time=now, last_activity=now)
            if subtask_id in self.subtask_start_times:
                state.start_time = self.subtask_start_times[subtask_id]
            self.subtasks[subtask_id] = state
        else:
            state = self.subtasks[subtask_id]
            # Reset stale session state if idle beyond wall clock timeout
            if (now - state.last_activity) > self.max_wall_clock_seconds:
                state = SubtaskCircuitState(subtask_id=subtask_id, start_time=now, last_activity=now)
                self.subtasks[subtask_id] = state
            else:
                state.last_activity = now

        return state

    def apply_sticky(self, subtask_id: str, alias: str) -> str:
        """After a session reaches tactical/opus, keep flash requests on tactical for
        `sticky_turns` turns so the model (and its prompt cache) does not flap.
        Opus never pins; it only hands the session to tactical afterwards."""
        state = self._get_or_create_state(subtask_id)
        if alias in ("gemini_tactical", "pro", "opus_apex", "opus"):
            state.sticky_left = self.sticky_turns
        elif alias in ("gemini_executor", "flash") and state.sticky_left > 0:
            state.sticky_left -= 1
            alias = "gemini_tactical"
        self._save_state()
        return alias

    def reset_subtask(self, subtask_id: str) -> None:
        if subtask_id in self.subtasks:
            del self.subtasks[subtask_id]
        if subtask_id in self.subtask_start_times:
            del self.subtask_start_times[subtask_id]
        self._save_state()

    def record_failure(
        self,
        subtask_id: str,
        traceback_text: str | None = None,
        diff_text: str | None = None,
        current_tier: str = "flash"
    ) -> BreakerDecision:
        state = self._get_or_create_state(subtask_id)
        # A non-empty diff with no traceback is a normal edit, not a failure.
        is_failure = bool(traceback_text) or (diff_text is not None and not diff_text.strip())
        if is_failure:
            state.iterations += 1

        decision: BreakerDecision | None = None

        # 1. Opus exhaustion check
        if is_failure and current_tier.lower() in ("opus", "opus_apex"):
            state.opus_attempts += 1
            if state.opus_attempts >= self.opus_max_attempts:
                summary = (
                    f"HANDOFF MANUSIA: Subtask '{subtask_id}' gagal 2x berturut-turut pada Tier Opus.\n"
                    f"Total iterasi: {state.iterations}.\n"
                    f"Traceback terakhir:\n{(traceback_text or '')[-500:]}"
                )
                decision = BreakerDecision(
                    action="human_handoff",
                    reason="opus_exhausted_2x",
                    opus_attempts=state.opus_attempts,
                    handoff_summary=summary
                )

        # 2. Wall-clock timeout check
        if not decision and is_failure:
            now = time.time()
            elapsed = now - state.start_time
            if elapsed >= self.max_wall_clock_seconds:
                decision = BreakerDecision(
                    action="force_escalate_one_tier",
                    reason="wall_clock_timeout_exceeded",
                    opus_attempts=state.opus_attempts
                )

        # 3. Max iterations check
        if not decision and is_failure and state.iterations >= self.max_iterations:
            decision = BreakerDecision(
                action="force_escalate_one_tier",
                reason="max_iterations_12_exceeded",
                opus_attempts=state.opus_attempts
            )

        # 4. Empty diff guard check
        if not decision and diff_text is not None:
            clean_diff = diff_text.strip()
            if not clean_diff:
                state.consecutive_empty_diffs += 1
                if state.consecutive_empty_diffs >= self.empty_diff_limit:
                    decision = BreakerDecision(
                        action="rollback_last_clean_commit_then_escalate_one_tier",
                        reason="consecutive_empty_diffs_3x",
                        opus_attempts=state.opus_attempts
                    )
            else:
                state.consecutive_empty_diffs = 0

                # 5. Diff oscillation check
                diff_hash = hashlib.sha256(clean_diff.encode("utf-8")).hexdigest()[:16]
                state.diff_hash_history.append(diff_hash)
                if len(state.diff_hash_history) > self.oscillation_window:
                    state.diff_hash_history.pop(0)

                # Check for oscillation pattern (e.g., A -> B -> A within window)
                if len(state.diff_hash_history) >= 3:
                    h = state.diff_hash_history
                    if h[-1] == h[-3] and h[-1] != h[-2]:
                        decision = BreakerDecision(
                            action="force_escalate_one_tier",
                            reason="diff_oscillation_detected",
                            opus_attempts=state.opus_attempts
                        )

        # 6. Identical error loop breaker (normalized traceback)
        if not decision and traceback_text:
            sig = normalize_traceback(traceback_text)
            if state.last_normalized_traceback == sig:
                state.consecutive_identical_tracebacks += 1
            else:
                state.last_normalized_traceback = sig
                state.consecutive_identical_tracebacks = 1

            if state.consecutive_identical_tracebacks >= self.identical_traceback_limit:
                decision = BreakerDecision(
                    action="force_escalate_one_tier",
                    reason="identical_error_loop_3x",
                    opus_attempts=state.opus_attempts
                )

        if not decision:
            decision = BreakerDecision(
                action="continue",
                opus_attempts=state.opus_attempts
            )

        self._save_state()
        return decision

