from __future__ import annotations

from sqlalchemy import select

from app.models import OpsGenerationJob


def test_mock_gateway_creates_only_review_draft_and_persists_lint(build_service, session_factory, user):
    service, gateway, review, dispatcher, redis_client = build_service(
        gateway=__import__("tests.conftest", fromlist=["MockGateway"]).MockGateway(
            response={"content": "本次活动稳赚，欢迎查看详情。"}
        )
    )

    result = service.run("copywriting", {"brief": "春季活动"}, user)

    assert result.status == "drafted"
    assert result.draft_id == "draft-test-1"
    assert result.lint_issues
    assert result.lint_issues[0]["code"] == "DISALLOWED_PHRASE"
    assert gateway.calls == 1
    assert review.calls == 1
    assert review.payloads[0]["job_id"] == result.job_id
    assert review.payloads[0]["lint_issues"] == result.lint_issues
    assert "send" not in dir(review)
    with session_factory() as session:
        row = session.scalar(select(OpsGenerationJob).where(OpsGenerationJob.id == result.job_id))
        assert row is not None
        assert row.status == "drafted"
        assert row.draft_id == result.draft_id
        assert row.lint_issues == result.lint_issues
        assert row.generated_content == "本次活动稳赚，欢迎查看详情。"


def test_long_input_is_truncated_and_warning_is_persisted(build_service, user):
    service, _, _, _, _ = build_service()
    result = service.run("copywriting", {"brief": "x" * 13000}, user)

    assert result.status == "drafted"
    assert any(issue["code"] == "INPUT_TRUNCATED" for issue in result.lint_issues)


def test_async_function_is_queued_without_inline_gateway_call(build_service, user):
    service, gateway, _, dispatcher, _ = build_service()

    result = service.run("proposal", {"project_scope": "仅虚构演示内容"}, user)

    assert result.status == "queued"
    assert dispatcher.job_ids == [result.job_id]
    assert gateway.calls == 0


def test_review_queue_unavailable_keeps_checkpoint_queued(build_service, user):
    from app.errors import ReviewQueueUnavailable
    from tests.conftest import MockReviewQueue

    service, gateway, review, _, _ = build_service(review=MockReviewQueue(error=ReviewQueueUnavailable()))
    result = service.run("copywriting", {"brief": "春季活动"}, user)

    assert result.status == "queued"
    assert result.generated_content is not None
    assert gateway.calls == 1
    review.error = None
    retried = service.process_job(result.job_id)
    assert retried.status == "drafted"
    assert gateway.calls == 1


def test_review_queue_failures_exhaust_attempts_and_persist_error(build_service, user):
    from app.errors import ReviewQueueUnavailable
    from tests.conftest import MockReviewQueue

    service, gateway, _, _, _ = build_service(
        review=MockReviewQueue(error=ReviewQueueUnavailable()),
        max_attempts=2,
    )
    first = service.run("copywriting", {"brief": "可重试但有上限"}, user)
    assert first.status == "queued"

    exhausted = service.process_job(first.job_id)
    assert exhausted.status == "failed"
    assert exhausted.error_code == "ALL_PROVIDERS_FAILED"
    assert gateway.calls == 1

