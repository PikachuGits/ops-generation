"""Loopback-only, test-data mock server; never use as a production entrypoint."""

from __future__ import annotations

import os
import threading
import uuid
from urllib.parse import urlparse

import redis

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.main import app, get_runtime
from app.runtime import Runtime
from app.service import OpsGenerationService


class LocalMockGateway:
    def generate(self, *, function_code: str, messages: list[dict[str, str]]) -> dict[str, str]:
        return {"content": f"[本地模拟网关/{function_code}] 已生成虚构演示草稿，请人工复核。"}


class LocalMockReviewQueue:
    def enqueue_draft(self, **kwargs) -> str:
        return f"mock-draft-{uuid.uuid4()}"


class LocalBackgroundDispatcher:
    def __init__(self) -> None:
        self.service: OpsGenerationService | None = None

    def enqueue(self, job_id: str) -> None:
        if self.service is None:
            raise RuntimeError("本地 mock worker 尚未就绪")
        thread = threading.Thread(target=self.service.process_job, args=(job_id,), daemon=True)
        thread.start()


def _require_loopback(url: str, setting_name: str) -> None:
    host = urlparse(url).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"本地 mock 模式仅允许 {setting_name} 连接 loopback 地址")


if os.getenv("OPS_GENERATION_LOCAL_MOCK") != "1":
    raise RuntimeError("仅本地模拟模式可加载：设置 OPS_GENERATION_LOCAL_MOCK=1")

settings = Settings.from_env()
_require_loopback(settings.database_url, "DATABASE_URL")
_require_loopback(settings.redis_url, "REDIS_URL")
engine = make_engine(settings.database_url)
session_factory = make_session_factory(engine)
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
redis_client.ping()
dispatcher = LocalBackgroundDispatcher()
service = OpsGenerationService(
    session_factory=session_factory,
    gateway=LocalMockGateway(),
    review_queue=LocalMockReviewQueue(),
    redis_client=redis_client,
    dispatcher=dispatcher,
    settings=settings,
)
dispatcher.service = service
runtime = Runtime(service=service, session_factory=session_factory, redis_client=redis_client)
app.dependency_overrides[get_runtime] = lambda: runtime
