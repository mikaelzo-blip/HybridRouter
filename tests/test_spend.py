import pytest
from src.observability.spend import TokenSpendTracker


def test_record_and_accumulate_tokens():
    tracker = TokenSpendTracker(anomaly_multiplier=3.0, baseline_p99_tokens=10000)
    tracker.record_usage("task-1", prompt_tokens=1500, completion_tokens=500, model="opus_apex")
    tracker.record_usage("task-1", prompt_tokens=2000, completion_tokens=1000, model="opus_apex")

    spend = tracker.get_subtask_spend("task-1")
    assert spend["total_tokens"] == 5000
    assert spend["call_count"] == 2
    assert spend["model_breakdown"]["opus_apex"] == 5000


def test_anomaly_detection_triggers_on_high_spend():
    tracker = TokenSpendTracker(anomaly_multiplier=3.0, baseline_p99_tokens=10000)
    
    # 5000 is under 30000 (3x 10000)
    tracker.record_usage("task-normal", prompt_tokens=4000, completion_tokens=1000, model="gemini_executor")
    is_anomaly, reason = tracker.check_anomaly("task-normal")
    assert not is_anomaly
    assert reason is None

    # Spend 35000 -> exceeds 3x p99 baseline (30000)
    tracker.record_usage("task-heavy", prompt_tokens=30000, completion_tokens=5000, model="opus_apex")
    is_anomaly, reason = tracker.check_anomaly("task-heavy")
    assert is_anomaly
    assert "token_spend_per_subtask_anomaly" in reason
    assert "35000" in reason


def test_dynamic_p99_anomaly_calculation():
    tracker = TokenSpendTracker(anomaly_multiplier=3.0, baseline_p99_tokens=5000)
    # Simulate a history of tasks spending around 2000-4000 tokens
    for i in range(20):
        tracker.record_usage(f"task-{i}", prompt_tokens=2500, completion_tokens=500, model="gemini_executor")
    
    # Now task-runaway spends 40,000 tokens
    tracker.record_usage("task-runaway", prompt_tokens=35000, completion_tokens=5000, model="opus_apex")
    is_anomaly, reason = tracker.check_anomaly("task-runaway")
    assert is_anomaly
    assert "exceeds" in reason


def test_daily_summary_aggregation():
    tracker = TokenSpendTracker()
    tracker.record_usage("task-a", prompt_tokens=100, completion_tokens=50, model="flash")
    tracker.record_usage("task-b", prompt_tokens=200, completion_tokens=100, model="pro")

    summary = tracker.get_daily_summary()
    assert summary["total_tokens"] == 450
    assert summary["total_calls"] == 2
    assert summary["models"]["flash"] == 150
    assert summary["models"]["pro"] == 300
