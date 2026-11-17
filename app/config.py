from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "postgresql+psycopg://ops:ops@localhost:5432/ops_generation"
    redis_url: str = "redis://localhost:6379/0"
    gateway_enabled: bool = False
    gateway_base_url: str = ""
    review_queue_base_url: str = ""
    idempotency_ttl_seconds: int = 60
    max_input_chars: int = 12000
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://ops:ops@localhost:5432/ops_generation"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            gateway_enabled=_as_bool(os.getenv("GATEWAY_ENABLED")),
            gateway_base_url=os.getenv("GATEWAY_BASE_URL", "").rstrip("/"),
            review_queue_base_url=os.getenv("REVIEW_QUEUE_BASE_URL", "").rstrip("/"),
            idempotency_ttl_seconds=max(1, int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "60"))),
            max_input_chars=max(256, int(os.getenv("MAX_INPUT_CHARS", "12000"))),
            max_attempts=max(1, int(os.getenv("MAX_ATTEMPTS", "3"))),
        )
