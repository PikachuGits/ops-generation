from __future__ import annotations

from typing import Protocol


class JobDispatcher(Protocol):
    def enqueue(self, job_id: str) -> None: ...


class NoopDispatcher:
    """Test-only dispatcher; production uses CeleryDispatcher."""

    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)


class CeleryDispatcher:
    def enqueue(self, job_id: str) -> None:
        from app.worker import process_generation_job

        process_generation_job.delay(job_id)
