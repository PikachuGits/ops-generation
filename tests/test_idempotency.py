from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import OpsGenerationIdempotency, OpsGenerationJob
from app.service import UserContext


def test_three_identical_submissions_create_one_job_and_call_clients_once(build_service, session_factory, user):
    service, gateway, review, _, _ = build_service()

    results = [service.run("copywriting", {"brief": "同一个需求"}, user) for _ in range(3)]

    assert len({result.job_id for result in results}) == 1
    assert all(result.status == "drafted" for result in results)
    assert gateway.calls == 1
    assert review.calls == 1
    with session_factory() as session:
        rows = session.scalars(select(OpsGenerationJob)).all()
        assert len(rows) == 1


def test_database_fallback_keeps_idempotency_when_redis_is_unavailable(build_service, user):
    service, gateway, review, _, _ = build_service(redis_client=None)

    first = service.run("copywriting", {"brief": "fallback"}, user)
    second = service.run("copywriting", {"brief": "fallback"}, user)

    assert first.job_id == second.job_id
    assert gateway.calls == 1
    assert review.calls == 1


def test_scope_includes_org_site_and_user(build_service, user):
    service, gateway, review, _, _ = build_service()
    inputs = {"brief": "same"}
    first = service.run("copywriting", inputs, user)
    repeated_same_scope = service.run("copywriting", inputs, user)
    other_org = UserContext(org_id="another-org", site_id=user.site_id, user_id=user.user_id)
    other_site = UserContext(org_id=user.org_id, site_id="another-site", user_id=user.user_id)
    other_user = UserContext(org_id=user.org_id, site_id=user.site_id, user_id="another-user")
    isolated = [
        service.run("copywriting", inputs, context)
        for context in (other_org, other_site, other_user)
    ]

    assert first.job_id == repeated_same_scope.job_id
    assert len({first.job_id, *(result.job_id for result in isolated)}) == 4
    assert gateway.calls == 4
    assert review.calls == 4


def test_database_reservation_expires_after_sixty_seconds(build_service, session_factory, user):
    service, _, _, _, _ = build_service(redis_client=None)
    first = service.run("copywriting", {"brief": "new window"}, user)
    with session_factory() as session:
        with session.begin():
            record = session.scalar(select(OpsGenerationIdempotency))
            assert record is not None
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    second = service.run("copywriting", {"brief": "new window"}, user)
    assert second.job_id != first.job_id
