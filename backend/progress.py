import asyncio
import time
from typing import Optional


class ProgressTracker:
    """In-memory progress tracking for estimation tasks."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    def create(self, task_id: str, platform: str, username: str):
        self._tasks[task_id] = {
            "task_id": task_id,
            "platform": platform,
            "username": username,
            "status": "starting",
            "progress": 0,
            "log": [],
            "error": None,
            "result": None,
            "started_at": time.time(),
            "completed_at": None,
        }

    def log(self, task_id: str, message: str, progress: Optional[float] = None):
        task = self._tasks.get(task_id)
        if not task:
            return
        task["log"].append({
            "time": time.time(),
            "message": message,
        })
        if progress is not None:
            task["progress"] = progress

    def set_status(self, task_id: str, status: str):
        task = self._tasks.get(task_id)
        if task:
            task["status"] = status
            if status in ("complete", "error"):
                task["completed_at"] = time.time()

    def set_result(self, task_id: str, result: dict):
        task = self._tasks.get(task_id)
        if task:
            task["result"] = result
            task["status"] = "complete"
            task["completed_at"] = time.time()
            task["progress"] = 100

    def set_error(self, task_id: str, error: str):
        task = self._tasks.get(task_id)
        if task:
            task["error"] = error
            task["status"] = "error"
            task["completed_at"] = time.time()

    def get(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def cleanup_old(self, max_age: int = 600):
        now = time.time()
        to_delete = [
            tid for tid, t in self._tasks.items()
            if t.get("completed_at") and (now - t["completed_at"]) > max_age
        ]
        for tid in to_delete:
            del self._tasks[tid]


# Global instance
progress = ProgressTracker()
