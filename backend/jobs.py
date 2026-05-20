"""Thread-safe in-memory job store for background analysis runs."""
from __future__ import annotations

import threading
import time
from typing import Any, Literal

JobStatus = Literal["queued", "running", "done", "error"]


class Job:
    def __init__(self, job_id: str, username: str) -> None:
        self.job_id = job_id
        self.username = username
        self.status: JobStatus = "queued"
        self.progress: str = "Queued..."
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "username": self.username,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, job_id: str, username: str) -> Job:
        job = Job(job_id, username)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update_progress(self, job_id: str, msg: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "running"
                job.progress = msg
                job.updated_at = time.time()

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "done"
                job.progress = "Analysis complete"
                job.result = result
                job.updated_at = time.time()

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "error"
                job.progress = "Analysis failed"
                job.error = error
                job.updated_at = time.time()

    def purge_old(self, max_age_seconds: int = 3600) -> None:
        """Remove jobs older than max_age_seconds to prevent memory leaks."""
        cutoff = time.time() - max_age_seconds
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if j.created_at < cutoff]
            for jid in stale:
                del self._jobs[jid]


# Singleton used by the FastAPI app
store = JobStore()
