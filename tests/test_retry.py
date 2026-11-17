from __future__ import annotations

from app.errors import ReviewQueueUnavailable
from app.service import UserContext
from tests.conftest import MockReviewQueue


def test_scoped_retry_reuses_queued_job_and_generated_checkpoint(build_service, user):
    review = MockReviewQueue(error=ReviewQueueUnavailable())
    service, gateway, _, dispatcher, _ = build_service(review=review)
    first = service.run("copywriting", {"brief": "需要复核"}, user)
    assert first.status == "queued"
    assert first.generated_content

    retried = service.retry_queued(first.job_id, user)
    assert retried is not None
    assert retried.job_id == first.job_id
    assert dispatcher.job_ids == [first.job_id]

    review.error = None
    finished = service.process_job(first.job_id)
    assert finished.status == "drafted"
    assert gateway.calls == 1


def test_job_is_not_visible_to_another_user(build_service, user):
    service, _, _, _, _ = build_service()
    result = service.run("proposal", {"project_scope": "演示"}, user)
    other = UserContext(org_id=user.org_id, site_id=user.site_id, user_id="other")
    assert service.get_job(result.job_id, other) is None
