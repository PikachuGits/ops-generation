from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.main import app, get_runtime
from app.queueing import NoopDispatcher
from app.runtime import Runtime
from app.service import OpsGenerationService, UserContext
from tests.conftest import MockGateway, MockReviewQueue


@pytest.fixture
def local_runtime():
    database_url = os.getenv("E2E_DATABASE_URL")
    redis_url = os.getenv("E2E_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("设置 E2E_DATABASE_URL 与 E2E_REDIS_URL 后运行本地 PG/Redis 集成测试")

    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        redis_client.ping()
        gateway = MockGateway(response={"content": "本地 mock 生成的虚构文案。"})
        review = MockReviewQueue(draft_id=f"draft-{uuid.uuid4()}")
        dispatcher = NoopDispatcher()
        settings = Settings(
            database_url=database_url,
            redis_url=redis_url,
            idempotency_ttl_seconds=60,
            max_input_chars=12000,
            max_attempts=3,
        )
        service = OpsGenerationService(
            session_factory=factory,
            gateway=gateway,
            review_queue=review,
            redis_client=redis_client,
            dispatcher=dispatcher,
            settings=settings,
        )
        runtime = Runtime(service=service, session_factory=factory, redis_client=redis_client)
        yield runtime, gateway, review, dispatcher
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        redis_client.close()
        engine.dispose()


def test_local_postgres_redis_mock_pipeline_and_api(local_runtime, api_request):
    runtime, gateway, review, dispatcher = local_runtime
    nonce = uuid.uuid4().hex
    user = UserContext(org_id=f"e2e-org-{nonce}", site_id="e2e-site", user_id="e2e-user")
    headers = {"X-Org-ID": user.org_id, "X-Site-ID": user.site_id, "X-User-ID": user.user_id}
    app.dependency_overrides[get_runtime] = lambda: runtime

    # Real local PostgreSQL + Redis, test-only adapters; no network calls to customer services.
    response = api_request(
        "POST",
        "/api/v1/generations",
        headers=headers,
        json_body={"function_code": "copywriting", "inputs": {"brief": f"虚构本地验收 {nonce}"}},
    )
    assert response.status_code == 202
    first = response.json()
    assert first["status"] == "drafted"
    assert first["draft_id"] == review.draft_id
    assert gateway.calls == review.calls == 1
    assert runtime.service.get_job(first["job_id"], user).attempt_count == 1

    repeated = api_request(
        "POST",
        "/api/v1/generations",
        headers=headers,
        json_body={"function_code": "copywriting", "inputs": {"brief": f"虚构本地验收 {nonce}"}},
    )
    assert repeated.status_code == 202
    assert repeated.json()["job_id"] == first["job_id"]
    assert gateway.calls == review.calls == 1

    other_user = {**headers, "X-User-ID": f"other-{nonce}"}
    hidden = api_request("GET", f"/api/v1/generations/{first['job_id']}", headers=other_user)
    assert hidden.status_code == 404

    queued = runtime.service.run("proposal", {"project_scope": f"虚构异步验收 {nonce}"}, user)
    assert queued.status == "queued"
    assert dispatcher.job_ids == [queued.job_id]
    completed = runtime.service.process_job(queued.job_id)
    assert completed.status == "drafted"

    # Exercise the 429 mapping through the same real DB/Redis service wiring.
    from app.errors import GatewayFailure

    failing = OpsGenerationService(
        session_factory=runtime.session_factory,
        gateway=MockGateway(error=GatewayFailure("QUOTA_EXCEEDED")),
        review_queue=MockReviewQueue(),
        redis_client=runtime.redis_client,
        dispatcher=dispatcher,
        settings=Settings(database_url=os.environ["E2E_DATABASE_URL"], redis_url=os.environ["E2E_REDIS_URL"]),
    )
    quota = failing.run("copywriting", {"brief": f"虚构额度错误验收 {nonce}"}, user)
    assert quota.status == "failed"
    assert quota.error_code == "QUOTA_EXCEEDED"
    assert quota.message == "模型额度已用尽，请稍后重试或联系管理员"


def test_local_postgres_redis_simultaneous_duplicate_submissions(local_runtime):
    runtime, gateway, review, _ = local_runtime
    nonce = uuid.uuid4().hex
    user = UserContext(org_id=f"e2e-race-org-{nonce}", site_id="e2e-site", user_id="e2e-user")
    inputs = {"brief": f"虚构并发幂等验收 {nonce}"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: runtime.service.run("copywriting", inputs, user), range(8)))

    assert len({result.job_id for result in results}) == 1
    assert all(result.status in {"queued", "drafted"} for result in results)
    final = runtime.service.get_job(results[0].job_id, user)
    assert final is not None and final.status == "drafted"
    assert gateway.calls == 1
    assert review.calls == 1
