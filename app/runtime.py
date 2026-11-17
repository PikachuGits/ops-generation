from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import redis
from sqlalchemy.orm import Session, sessionmaker

from app.clients.gateway import DisabledGatewayClient, HttpGatewayClient
from app.clients.review_queue import DisabledReviewQueueClient, HttpReviewQueueClient
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.queueing import CeleryDispatcher, JobDispatcher
from app.service import OpsGenerationService


@dataclass(slots=True)
class Runtime:
    service: OpsGenerationService
    session_factory: sessionmaker[Session]
    redis_client: Any


def build_runtime(settings: Settings | None = None, *, dispatcher: JobDispatcher | None = None) -> Runtime:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    gateway = (
        HttpGatewayClient(settings.gateway_base_url)
        if settings.gateway_enabled and settings.gateway_base_url
        else DisabledGatewayClient()
    )
    review_queue = (
        HttpReviewQueueClient(settings.review_queue_base_url)
        if settings.review_queue_base_url
        else DisabledReviewQueueClient()
    )
    service = OpsGenerationService(
        session_factory=session_factory,
        gateway=gateway,
        review_queue=review_queue,
        redis_client=redis_client,
        dispatcher=dispatcher or CeleryDispatcher(),
        settings=settings,
    )
    return Runtime(service=service, session_factory=session_factory, redis_client=redis_client)
