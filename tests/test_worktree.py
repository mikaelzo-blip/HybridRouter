import pytest
from pathlib import Path
from src.isolation.worktree import (
    TaskNode,
    WorktreeTaskGraph,
    WorktreeManager,
    SequentialMergeQueue,
)


def test_task_graph_validates_disjoint_files_owned():
    graph = WorktreeTaskGraph()
    graph.add_task(TaskNode(task_id="t1", files_owned={"src/service/auth.py", "tests/test_auth.py"}))
    graph.add_task(TaskNode(task_id="t2", files_owned={"src/service/order.py", "tests/test_order.py"}))

    is_valid, overlaps = graph.validate_disjoint_ownership()
    assert is_valid is True
    assert overlaps == {}


def test_task_graph_detects_overlapping_files_owned():
    graph = WorktreeTaskGraph()
    graph.add_task(TaskNode(task_id="t1", files_owned={"src/common/db.py", "src/service/auth.py"}))
    graph.add_task(TaskNode(task_id="t2", files_owned={"src/common/db.py", "src/service/order.py"}))

    is_valid, overlaps = graph.validate_disjoint_ownership()
    assert is_valid is False
    assert "src/common/db.py" in overlaps
    assert set(overlaps["src/common/db.py"]) == {"t1", "t2"}


def test_merge_queue_processes_sequentially(tmp_path):
    queue = SequentialMergeQueue()
    results = []

    def mock_merge_and_test(task_id: str, files: set[str]) -> bool:
        results.append(task_id)
        return True

    queue.enqueue(task_id="task-1", files_owned={"a.py"}, merge_fn=mock_merge_and_test)
    queue.enqueue(task_id="task-2", files_owned={"b.py"}, merge_fn=mock_merge_and_test)

    summary = queue.process_all()
    assert results == ["task-1", "task-2"]
    assert summary["successful"] == ["task-1", "task-2"]
    assert summary["failed"] == []


def test_merge_queue_flags_conflicts_for_pro_resolution():
    queue = SequentialMergeQueue()

    def mock_merge_failing(task_id: str, files: set[str]) -> bool:
        if task_id == "task-conflict":
            return False
        return True

    queue.enqueue(task_id="task-ok", files_owned={"ok.py"}, merge_fn=mock_merge_failing)
    queue.enqueue(task_id="task-conflict", files_owned={"conflict.py"}, merge_fn=mock_merge_failing)

    summary = queue.process_all()
    assert summary["successful"] == ["task-ok"]
    assert len(summary["failed"]) == 1
    assert summary["failed"][0]["task_id"] == "task-conflict"
    assert summary["failed"][0]["resolution_tier"] == "gemini_tactical"  # Pro resolves conflicts
