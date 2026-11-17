from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.main import app
from app.models import Base
from app.queueing import NoopDispatcher
from app.service import OpsGenerationService, UserContext


@pytest.fixture
def api_request():
    def _request(method: str, path: str, *, headers: dict[str, str] | None = None,
                 json_body: dict[str, Any] | None = None) -> httpx.Response:
        async def _send() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                return await client.request(method, path, headers=headers, json=json_body)
        return asyncio.run(_send())
    return _request


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> bool | None:
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


class MockGateway:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else {"content": "春季活动上线，欢迎查看详情。"}
        self.error = error
        self.calls = 0
        self.messages: list[dict[str, str]] | None = None

    def generate(self, *, function_code: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls += 1
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.response


class MockReviewQueue:
    def __init__(self, draft_id: str = "draft-test-1", error: Exception | None = None) -> None:
        self.draft_id = draft_id
        self.error = error
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def enqueue_draft(self, **kwargs: Any) -> str:
        self.calls += 1
        self.payloads.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.draft_id


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def user() -> UserContext:
    return UserContext(org_id="org-demo", site_id="site-demo", user_id="user-demo")


_DEFAULT_REDIS = object()


@pytest.fixture
def build_service(session_factory: sessionmaker[Session]) -> Callable[..., tuple[OpsGenerationService, MockGateway, MockReviewQueue, NoopDispatcher, FakeRedis | None]]:
    def _build(*, gateway: MockGateway | None = None, review: MockReviewQueue | None = None,
               dispatcher: NoopDispatcher | None = None, redis_client: FakeRedis | None | object = _DEFAULT_REDIS,
               max_attempts: int = 3) -> tuple[OpsGenerationService, MockGateway, MockReviewQueue, NoopDispatcher, FakeRedis | None]:
        gateway = gateway or MockGateway()
        review = review or MockReviewQueue()
        dispatcher = dispatcher or NoopDispatcher()
        if redis_client is _DEFAULT_REDIS:
            redis_client = FakeRedis()
        settings = Settings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
            idempotency_ttl_seconds=60,
            max_input_chars=12000,
            max_attempts=max_attempts,
        )
        service = OpsGenerationService(
            session_factory=session_factory,
            gateway=gateway,
            review_queue=review,
            redis_client=redis_client,
            dispatcher=dispatcher,
            settings=settings,
        )
        return service, gateway, review, dispatcher, redis_client
    return _build
