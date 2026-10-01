from __future__ import annotations

import asyncio
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    kind: str
    project_id: str
    status: str = "queued"
    stage: str = "Queued"
    progress: float = 0
    message: str = "Waiting to start"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    result: dict[str, Any] | None = None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    def __init__(self, workers: int = 2):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-agent")

    def create(self, kind: str, project_id: str, fn: Callable[..., dict[str, Any]]) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, project_id=project_id)
        with self._lock:
            self.jobs[job.id] = job
        self._pool.submit(self._run, job.id, fn)
        return job

    def _run(self, job_id: str, fn: Callable[..., dict[str, Any]]) -> None:
        self.update(job_id, status="running", stage="Starting", message="Preparing local workspace")
        try:
            result = fn(lambda **kw: self.update(job_id, **kw))
            self.update(job_id, status="completed", stage="Complete", progress=100, message="Finished", result=result)
        except Exception as exc:
            traceback.print_exc()
            self.update(
                job_id,
                status="failed",
                stage="Failed",
                message=str(exc),
                error=f"{type(exc).__name__}: {exc}",
            )

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.progress = max(0, min(100, float(job.progress)))
            job.updated_at = now()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [j.public() for j in self.jobs.values() if j.project_id == project_id]
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:20]


jobs = JobManager()
