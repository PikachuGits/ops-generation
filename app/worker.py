from __future__ import annotations

from celery import Celery

from app.config import Settings
from app.runtime import build_runtime

settings = Settings.from_env()
celery_app = Celery("ops_generation", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_connection_retry_on_startup=True,
)


@celery_app.task(bind=True, name="app.worker.process_generation_job", max_retries=10)
def process_generation_job(self, job_id: str) -> dict[str, str]:
    service = build_runtime().service
    result = service.process_job(job_id)
    if result.status == "queued":
        if self.request.retries >= settings.max_attempts + 6:
            service.fail_exhausted_job(job_id)
            result = service.get_job_by_id(job_id)
        else:
            raise self.retry(countdown=min(2 ** self.request.retries, 30))
    return {"job_id": result.job_id, "status": result.status}
