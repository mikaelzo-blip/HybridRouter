from collections import defaultdict
from dataclasses import dataclass, field
import logging
from pathlib import Path
import subprocess
from typing import Any, Callable

logger = logging.getLogger("hybrid_router.isolation")


@dataclass
class TaskNode:
    task_id: str
    files_owned: set[str] = field(default_factory=set)
    status: str = "pending"


class WorktreeTaskGraph:
    def __init__(self):
        self.tasks: dict[str, TaskNode] = {}

    def add_task(self, task: TaskNode) -> None:
        self.tasks[task.task_id] = task

    def validate_disjoint_ownership(self) -> tuple[bool, dict[str, list[str]]]:
        """Validates that no two tasks own overlapping files.

        Returns (is_valid, overlaps_map) where overlaps_map is file -> [task_id, ...]
        """
        file_to_tasks: dict[str, list[str]] = defaultdict(list)
        for task_id, task in self.tasks.items():
            for f in task.files_owned:
                file_to_tasks[f].append(task_id)

        overlaps: dict[str, list[str]] = {
            f: tids for f, tids in file_to_tasks.items() if len(tids) > 1
        }
        return len(overlaps) == 0, overlaps


class SequentialMergeQueue:
    def __init__(self):
        self._queue: list[dict[str, Any]] = []

    def enqueue(
        self,
        task_id: str,
        files_owned: set[str],
        merge_fn: Callable[[str, set[str]], bool],
    ) -> None:
        self._queue.append({
            "task_id": task_id,
            "files_owned": files_owned,
            "merge_fn": merge_fn,
        })

    def process_all(self) -> dict[str, Any]:
        successful: list[str] = []
        failed: list[dict[str, Any]] = []

        while self._queue:
            item = self._queue.pop(0)
            task_id = item["task_id"]
            files = item["files_owned"]
            merge_fn = item["merge_fn"]

            try:
                ok = merge_fn(task_id, files)
                if ok:
                    successful.append(task_id)
                else:
                    failed.append({
                        "task_id": task_id,
                        "resolution_tier": "gemini_tactical",
                        "reason": "merge_conflict_or_test_failure",
                    })
            except Exception as e:
                failed.append({
                    "task_id": task_id,
                    "resolution_tier": "gemini_tactical",
                    "reason": str(e),
                })

        return {
            "successful": successful,
            "failed": failed,
        }


class WorktreeManager:
    """Manages creation and cleanup of isolated git worktrees."""

    @staticmethod
    def create_worktree(repo_dir: Path, worktree_path: Path, branch_name: str) -> None:
        cmd = ["git", "worktree", "add", "-b", branch_name, str(worktree_path)]
        res = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to create worktree: {res.stderr}")

    @staticmethod
    def remove_worktree(repo_dir: Path, worktree_path: Path, branch_name: str | None = None) -> None:
        cmd = ["git", "worktree", "remove", "--force", str(worktree_path)]
        subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
        if branch_name:
            subprocess.run(["git", "branch", "-D", branch_name], cwd=repo_dir, capture_output=True, text=True)
