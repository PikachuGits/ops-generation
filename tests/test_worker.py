from __future__ import annotations

from types import SimpleNamespace

import pytest
from celery.exceptions import Retry

import app.worker as worker


class StubService:
    def __init__(self, status: str) -> None:
        self.status = status
        self.failed = False

    def process_job(self, job_id: str):
        return SimpleNamespace(job_id=job_id, status=self.status)

    def fail_exhausted_job(self, job_id: str):
        self.failed = True
        self.status = "failed"
        return SimpleNamespace(job_id=job_id, status=self.status)

    def get_job_by_id(self, job_id: str):
        return SimpleNamespace(job_id=job_id, status=self.status)


def test_worker_acknowledges_completed_job(monkeypatch):
    service = StubService("drafted")
    monkeypatch.setattr(worker, "build_runtime", lambda: SimpleNamespace(service=service))

    assert worker.process_generation_job.run("job-1") == {"job_id": "job-1", "status": "drafted"}


def test_worker_retries_queued_job(monkeypatch):
    service = StubService("queued")
    monkeypatch.setattr(worker, "build_runtime", lambda: SimpleNamespace(service=service))

    worker.process_generation_job.push_request(retries=0)
    try:
        with pytest.raises(Retry):
            worker.process_generation_job.run("job-2")
    finally:
        worker.process_generation_job.pop_request()


def test_worker_marks_queued_job_failed_after_retry_budget(monkeypatch):
    service = StubService("queued")
    monkeypatch.setattr(worker, "build_runtime", lambda: SimpleNamespace(service=service))

    worker.process_generation_job.push_request(retries=worker.settings.max_attempts + 6)
    try:
        result = worker.process_generation_job.run("job-3")
    finally:
        worker.process_generation_job.pop_request()

    assert service.failed is True
    assert result == {"job_id": "job-3", "status": "failed"}
