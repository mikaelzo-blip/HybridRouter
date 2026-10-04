from collections import defaultdict
from dataclasses import dataclass, field
import logging
import math
from typing import Any

logger = logging.getLogger("hybrid_router.spend")


@dataclass
class SubtaskSpend:
    subtask_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    call_count: int = 0
    model_breakdown: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenSpendTracker:
    def __init__(
        self,
        anomaly_multiplier: float = 3.0,
        baseline_p99_tokens: int = 15000,
    ):
        self.anomaly_multiplier = anomaly_multiplier
        self.baseline_p99_tokens = baseline_p99_tokens
        self.subtasks: dict[str, SubtaskSpend] = {}
        self.daily_total_prompt = 0
        self.daily_total_completion = 0
        self.daily_total_calls = 0
        self.daily_model_tokens: dict[str, int] = defaultdict(int)

    def record_usage(
        self,
        subtask_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
    ) -> None:
        if len(self.subtasks) > 1000:
            # Evict oldest subtasks to prevent unbounded memory growth
            for k in list(self.subtasks.keys())[:200]:
                self.subtasks.pop(k, None)

        if subtask_id not in self.subtasks:
            self.subtasks[subtask_id] = SubtaskSpend(subtask_id=subtask_id)

        st = self.subtasks[subtask_id]
        st.prompt_tokens += prompt_tokens
        st.completion_tokens += completion_tokens
        st.call_count += 1
        st.model_breakdown[model] += prompt_tokens + completion_tokens

        # Daily aggregates
        self.daily_total_prompt += prompt_tokens
        self.daily_total_completion += completion_tokens
        self.daily_total_calls += 1
        self.daily_model_tokens[model] += prompt_tokens + completion_tokens

    def get_subtask_spend(self, subtask_id: str) -> dict[str, Any]:
        st = self.subtasks.get(subtask_id)
        if not st:
            return {
                "subtask_id": subtask_id,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "call_count": 0,
                "model_breakdown": {},
            }
        return {
            "subtask_id": subtask_id,
            "total_tokens": st.total_tokens,
            "prompt_tokens": st.prompt_tokens,
            "completion_tokens": st.completion_tokens,
            "call_count": st.call_count,
            "model_breakdown": dict(st.model_breakdown),
        }

    def _calculate_p99_tokens(self, exclude_subtask: str | None = None) -> float:
        totals = [
            st.total_tokens
            for sid, st in self.subtasks.items()
            if st.total_tokens > 0 and sid != exclude_subtask
        ]
        if len(totals) < 5:
            return float(self.baseline_p99_tokens)

        sorted_totals = sorted(totals)
        # 99th percentile index
        k = (len(sorted_totals) - 1) * 0.99
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            p99 = sorted_totals[int(k)]
        else:
            d0 = sorted_totals[int(f)] * (c - k)
            d1 = sorted_totals[int(c)] * (k - f)
            p99 = d0 + d1

        return max(float(self.baseline_p99_tokens), float(p99))

    def check_anomaly(self, subtask_id: str) -> tuple[bool, str | None]:
        st = self.subtasks.get(subtask_id)
        if not st:
            return False, None

        current_tokens = st.total_tokens
        p99 = self._calculate_p99_tokens(exclude_subtask=subtask_id)
        threshold = p99 * self.anomaly_multiplier

        if current_tokens > threshold:
            reason = (
                f"token_spend_per_subtask_anomaly: subtask '{subtask_id}' spent {current_tokens} tokens, "
                f"which exceeds {self.anomaly_multiplier}x p99 threshold ({threshold:.0f} tokens)"
            )
            logger.warning(reason)
            return True, reason

        return False, None

    def get_daily_summary(self) -> dict[str, Any]:
        return {
            "total_tokens": self.daily_total_prompt + self.daily_total_completion,
            "prompt_tokens": self.daily_total_prompt,
            "completion_tokens": self.daily_total_completion,
            "total_calls": self.daily_total_calls,
            "models": dict(self.daily_model_tokens),
            "tracked_subtasks_count": len(self.subtasks),
        }
