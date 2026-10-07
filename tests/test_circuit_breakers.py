import time
import pytest
from src.breakers.normalizer import normalize_traceback
from src.breakers.circuit import CircuitBreakerTracker, BreakerEvent, BreakerDecision


def test_normalize_traceback_strips_paths_and_addresses():
    raw_tb1 = """
Traceback (most recent call last):
  File "C:\\Projects\\HybridRouter\\src\\server.py", line 42, in run
    obj = <MyClass object at 0x000001E8B2C4F0>
  File "C:\\Users\\Fikri\\AppData\\Local\\Temp\\tmp123.py", line 10, in execute
    raise ValueError("Invalid state at 2026-10-02T23:15:30.123456Z")
ValueError: Invalid state at 2026-10-02T23:15:30.123456Z
"""
    raw_tb2 = """
Traceback (most recent call last):
  File "/c/different/path/src/server.py", line 42, in run
    obj = <MyClass object at 0x7ffd98a123b0>
  File "/tmp/scratch_dir/tmp999.py", line 10, in execute
    raise ValueError("Invalid state at 2026-10-02T23:18:45.654321Z")
ValueError: Invalid state at 2026-10-02T23:18:45.654321Z
"""
    sig1 = normalize_traceback(raw_tb1)
    sig2 = normalize_traceback(raw_tb2)
    assert sig1 == sig2
    assert "0x" not in sig1
    assert "2026-10-02" not in sig1


def test_identical_error_loop_breaker():
    tracker = CircuitBreakerTracker()
    subtask_id = "task-1"
    
    tb = "Traceback (most recent call last):\n  File 'a.py', line 1\nZeroDivisionError: division by zero"
    
    # 1st time
    dec1 = tracker.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="+ line1", current_tier="flash")
    assert dec1.action == "continue"
    
    # 2nd time
    dec2 = tracker.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="+ line2", current_tier="flash")
    assert dec2.action == "continue"
    
    # 3rd consecutive identical traceback
    dec3 = tracker.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="+ line3", current_tier="flash")
    assert dec3.action == "force_escalate_one_tier"
    assert dec3.reason == "identical_error_loop_3x"


def test_diff_oscillation_breaker():
    tracker = CircuitBreakerTracker()
    subtask_id = "task-2"
    
    diff_a = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-val = 1\n+val = 2"
    diff_b = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-val = 2\n+val = 1"
    
    # A -> B -> A pattern within 6 iterations
    tracker.record_failure(subtask_id=subtask_id, traceback_text="err1", diff_text=diff_a, current_tier="flash")
    tracker.record_failure(subtask_id=subtask_id, traceback_text="err2", diff_text=diff_b, current_tier="flash")
    dec = tracker.record_failure(subtask_id=subtask_id, traceback_text="err3", diff_text=diff_a, current_tier="flash")
    
    assert dec.action == "force_escalate_one_tier"
    assert dec.reason == "diff_oscillation_detected"


def test_empty_diff_guard():
    tracker = CircuitBreakerTracker()
    subtask_id = "task-3"
    
    # 1st empty diff
    dec1 = tracker.record_failure(subtask_id=subtask_id, traceback_text="err1", diff_text="", current_tier="flash")
    assert dec1.action == "continue"
    
    # 2nd empty diff
    dec2 = tracker.record_failure(subtask_id=subtask_id, traceback_text="err2", diff_text="   \n  ", current_tier="flash")
    assert dec2.action == "continue"
    
    # 3rd consecutive empty diff -> rollback and escalate
    dec3 = tracker.record_failure(subtask_id=subtask_id, traceback_text="err3", diff_text="", current_tier="flash")
    assert dec3.action == "rollback_last_clean_commit_then_escalate_one_tier"
    assert dec3.reason == "consecutive_empty_diffs_3x"


def test_subtask_iteration_limit():
    tracker = CircuitBreakerTracker()
    subtask_id = "task-4"
    
    for i in range(1, 12):
        dec = tracker.record_failure(subtask_id=subtask_id, traceback_text=f"err{i}", diff_text=f"+ line{i}", current_tier="flash")
        assert dec.action == "continue"
        
    dec12 = tracker.record_failure(subtask_id=subtask_id, traceback_text="err12", diff_text="+ line12", current_tier="flash")
    assert dec12.action == "force_escalate_one_tier"
    assert dec12.reason == "max_iterations_12_exceeded"


def test_subtask_wall_clock_timeout(monkeypatch):
    tracker = CircuitBreakerTracker()
    subtask_id = "task-5"
    
    # Simulate start time 31 minutes ago
    start_time = time.time() - (31 * 60)
    tracker.subtask_start_times[subtask_id] = start_time
    
    dec = tracker.record_failure(subtask_id=subtask_id, traceback_text="err", diff_text="+ x", current_tier="flash")
    assert dec.action == "force_escalate_one_tier"
    assert dec.reason == "wall_clock_timeout_exceeded"


def test_opus_exhaustion_human_handoff():
    tracker = CircuitBreakerTracker()
    subtask_id = "task-6"
    
    # 1st Opus rescue attempt fails
    dec1 = tracker.record_failure(subtask_id=subtask_id, traceback_text="opus_err1", diff_text="+ patch1", current_tier="opus")
    assert dec1.action == "continue"
    assert dec1.opus_attempts == 1
    
    # 2nd Opus rescue attempt fails -> human handoff
    dec2 = tracker.record_failure(subtask_id=subtask_id, traceback_text="opus_err2", diff_text="+ patch2", current_tier="opus")
    assert dec2.action == "human_handoff"
    assert dec2.reason == "opus_exhausted_2x"
    assert dec2.handoff_summary is not None


def test_circuit_breaker_persistence(tmp_path):
    state_file = str(tmp_path / "breaker_state.json")
    tracker1 = CircuitBreakerTracker(state_file=state_file)
    subtask_id = "persist_task"

    tb = "Traceback (most recent call last):\n  File 'x.py', line 1\nRuntimeError: test"
    tracker1.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="+ x", current_tier="flash")
    tracker1.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="+ x2", current_tier="flash")

    # Verify tracker2 reloads state from disk
    tracker2 = CircuitBreakerTracker(state_file=state_file)
    state = tracker2.subtasks[subtask_id]
    assert state.consecutive_identical_tracebacks == 2
    assert state.iterations == 2

    # 3rd failure on reloaded instance triggers identical_error_loop_3x
    dec = tracker2.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="+ x3", current_tier="flash")
    assert dec.action == "force_escalate_one_tier"
    assert dec.reason == "identical_error_loop_3x"


def test_circuit_breaker_record_success_resets_streaks():
    tracker = CircuitBreakerTracker()
    subtask_id = "success_task"

    tb = "Traceback (most recent call last):\n  File 'x.py', line 1\nRuntimeError: test"
    tracker.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="", current_tier="flash")
    tracker.record_failure(subtask_id=subtask_id, traceback_text=tb, diff_text="", current_tier="flash")

    state = tracker.subtasks[subtask_id]
    assert state.consecutive_identical_tracebacks == 2
    assert state.consecutive_empty_diffs == 2

    # Success occurs (clean test pass)
    tracker.record_success(subtask_id=subtask_id)

    assert state.consecutive_identical_tracebacks == 0
    assert state.consecutive_empty_diffs == 0
    assert state.last_normalized_traceback is None
    # Success clears the failure budget and wall-clock window
    assert state.iterations == 0


def test_success_resets_iterations_and_wall_clock():
    tracker = CircuitBreakerTracker()
    sid = "long_session"
    state = tracker._get_or_create_state(sid)
    state.start_time = time.time() - 31 * 60
    state.iterations = 11
    tracker.record_success(subtask_id=sid)
    assert state.iterations == 0
    assert time.time() - state.start_time < 5
    tb = "Traceback (most recent call last):\nValueError: x"
    dec = tracker.record_failure(sid, traceback_text=tb, current_tier="flash")
    assert dec.action == "continue"


def test_clean_diff_without_traceback_is_not_a_failure():
    """Normal patch/write_file edits must not burn iterations or trip the wall clock."""
    tracker = CircuitBreakerTracker()
    sid = "editing_session"
    state = tracker._get_or_create_state(sid)
    state.start_time = time.time() - 31 * 60
    for i in range(20):
        diff = "--- a\n+++ a\n+ edit %d" % i
        dec = tracker.record_failure(sid, traceback_text=None, diff_text=diff, current_tier="flash")
        assert dec.action == "continue", (i, dec.reason)
    assert state.iterations == 0


def test_sticky_tier_prevents_downgrade_flapping():
    tracker = CircuitBreakerTracker(sticky_turns=3)
    sid = "sticky"
    assert tracker.apply_sticky(sid, "gemini_tactical") == "gemini_tactical"
    # next turns routed to flash stay on tactical for sticky_turns, then drop
    assert [tracker.apply_sticky(sid, "gemini_executor") for _ in range(4)] == [
        "gemini_tactical", "gemini_tactical", "gemini_tactical", "gemini_executor"
    ]


def test_sticky_tier_caps_at_tactical_and_ignores_other_aliases():
    tracker = CircuitBreakerTracker(sticky_turns=3)
    sid = "sticky2"
    # Opus rescue must not pin the session to Opus (burns Claude quota)
    assert tracker.apply_sticky(sid, "opus_apex") == "opus_apex"
    assert tracker.apply_sticky(sid, "gemini_executor") == "gemini_tactical"
    # non-ladder alias (review target) passes through untouched
    assert tracker.apply_sticky(sid, "sonnet_fallback") == "sonnet_fallback"
    # sessions are independent
    assert tracker.apply_sticky("other", "gemini_executor") == "gemini_executor"
