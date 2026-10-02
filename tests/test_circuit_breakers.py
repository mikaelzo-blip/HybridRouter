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
