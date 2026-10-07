"""The `circuit_breakers:` block in the routing YAML must drive CircuitBreakerTracker."""
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.breakers.circuit import CircuitBreakerTracker
from src.config import AppSettings
from src.schemas import CircuitBreakersConfig
from src.server.app import create_app


def _cfg(**overrides) -> CircuitBreakersConfig:
    return CircuitBreakersConfig.model_validate(overrides)


# ── from_config maps every field ──

def test_from_config_maps_limits():
    tracker = CircuitBreakerTracker.from_config(_cfg(
        identical_error_loop={"consecutive": 5},
        diff_oscillation={"window": 8},
        empty_diff_guard={"consecutive_empty_diffs": 4},
        subtask_limits={"max_iterations": 20, "max_wall_clock_minutes": 45},
        opus_exhausted={"max_attempts": 3},
    ))
    assert tracker.identical_traceback_limit == 5
    assert tracker.oscillation_window == 8
    assert tracker.empty_diff_limit == 4
    assert tracker.max_iterations == 20
    assert tracker.max_wall_clock_seconds == 45 * 60
    assert tracker.opus_max_attempts == 3


def test_defaults_match_previous_hardcoded_values():
    from_cfg = CircuitBreakerTracker.from_config(CircuitBreakersConfig())
    plain = CircuitBreakerTracker()
    for attr in ("identical_traceback_limit", "oscillation_window", "empty_diff_limit",
                 "max_iterations", "max_wall_clock_seconds", "opus_max_attempts"):
        assert getattr(from_cfg, attr) == getattr(plain, attr), attr


def test_configured_identical_error_limit_is_enforced():
    tracker = CircuitBreakerTracker.from_config(_cfg(identical_error_loop={"consecutive": 5}))
    for _ in range(4):
        assert tracker.record_failure("s", traceback_text="ValueError: x", current_tier="flash").action == "continue"
    assert tracker.record_failure("s", traceback_text="ValueError: x", current_tier="flash").action == "force_escalate_one_tier"


def test_configured_opus_limit_is_enforced():
    tracker = CircuitBreakerTracker.from_config(_cfg(opus_exhausted={"max_attempts": 3}))
    for i in range(2):
        assert tracker.record_failure("s", traceback_text=f"err {i}", current_tier="opus_apex").action == "continue"
    dec = tracker.record_failure("s", traceback_text="err 2", current_tier="opus_apex")
    assert dec.action == "human_handoff"
    assert "batas 3x" in dec.handoff_summary


# ── configured actions are used ──

def test_configured_action_replaces_default():
    tracker = CircuitBreakerTracker.from_config(_cfg(identical_error_loop={"consecutive": 2, "action": "human_handoff"}))
    tracker.record_failure("s", traceback_text="ValueError: x", current_tier="flash")
    dec = tracker.record_failure("s", traceback_text="ValueError: x", current_tier="flash")
    assert dec.action == "human_handoff"
    assert dec.reason == "identical_error_loop_3x"   # reason ids stay stable for clients
    assert dec.handoff_summary and "identical_error_loop_3x" in dec.handoff_summary


@pytest.mark.parametrize("bad", [
    {"identical_error_loop": {"action": "explode"}},
    {"identical_error_loop": {"consecutive": 0}},
    {"identical_error_loop": {"signature": "raw"}},
    {"diff_oscillation": {"window": 2}},
    {"subtask_limits": {"max_iterations": 0}},
    {"test_tampering": {"action": "force_escalate_one_tier"}},
])
def test_invalid_breaker_config_rejected_at_load(bad):
    with pytest.raises(ValidationError):
        CircuitBreakersConfig.model_validate(bad)


# ── oscillation honours the window ──

def _feed(tracker, diffs):
    return [tracker.record_failure("s", diff_text=d, current_tier="flash") for d in diffs]


def test_oscillation_detected_across_window_not_only_adjacent():
    tracker = CircuitBreakerTracker.from_config(_cfg(diff_oscillation={"window": 6}))
    decisions = _feed(tracker, ["+a", "+b", "+c", "+a"])        # A-B-C-A
    assert [d.action for d in decisions[:3]] == ["continue"] * 3
    assert decisions[3].reason == "diff_oscillation_detected"


def test_oscillation_outside_window_is_ignored():
    tracker = CircuitBreakerTracker.from_config(_cfg(diff_oscillation={"window": 3}))
    decisions = _feed(tracker, ["+a", "+b", "+c", "+a"])        # A fell out of a window of 3
    assert all(d.action == "continue" for d in decisions)


def test_same_diff_repeated_back_to_back_is_not_oscillation():
    tracker = CircuitBreakerTracker.from_config(CircuitBreakersConfig())
    decisions = _feed(tracker, ["+a", "+a", "+a"])
    assert all(d.reason != "diff_oscillation_detected" for d in decisions)


# ── end to end: the server reads the YAML ──

def test_server_uses_breaker_values_from_yaml(tmp_path, monkeypatch):
    with open("config/9router-production.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw["circuit_breakers"]["identical_error_loop"]["consecutive"] = 7
    raw["circuit_breakers"]["subtask_limits"]["max_iterations"] = 25
    raw["circuit_breakers"]["opus_exhausted"]["max_attempts"] = 4
    cfg_path = tmp_path / "router.yaml"
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("ROUTING_CONFIG_PATH", str(cfg_path))

    app = create_app(AppSettings())
    tracker = app.state.circuit_tracker
    assert tracker.identical_traceback_limit == 7
    assert tracker.max_iterations == 25
    assert tracker.opus_max_attempts == 4

    status = TestClient(app).get("/breakers/status?session_id=x").json()
    assert status["should_escalate"] is False
