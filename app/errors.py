from __future__ import annotations

from dataclasses import dataclass


ERROR_MESSAGES = {
    "QUOTA_EXCEEDED": "模型额度已用尽，请稍后重试或联系管理员",
    "SCHEMA_VALIDATION_FAILED": "生成结果不符合约定格式，已记录待人工复核",
    "ALL_PROVIDERS_FAILED": "所有模型服务商均不可用，请稍后重试",
    "GW_DISABLED": "模型网关已关闭，请联系管理员开启",
}
FALLBACK_ERROR_MESSAGE = "生成失败，请稍后重试"


def user_message(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    return ERROR_MESSAGES.get(error_code, FALLBACK_ERROR_MESSAGE)


@dataclass(slots=True)
class GatewayFailure(Exception):
    error_code: str

    def __str__(self) -> str:
        return self.error_code


class ReviewQueueUnavailable(Exception):
    """Retryable review-queue outage; leave the job queued."""


class RequestInputError(ValueError):
    """Invalid user input; surfaced as HTTP 422 without an internal error code."""
