from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.egress import authorized_egress
from app.errors import ReviewQueueUnavailable


class ReviewQueueClient(Protocol):
    def enqueue_draft(self, *, job_id: str, org_id: str, site_id: str, created_by: str,
                      function_code: str, content: str, lint_issues: list[dict[str, Any]]) -> str: ...


class DisabledReviewQueueClient:
    def enqueue_draft(self, *, job_id: str, org_id: str, site_id: str, created_by: str,
                      function_code: str, content: str, lint_issues: list[dict[str, Any]]) -> str:
        raise ReviewQueueUnavailable("复核队列未配置")


class HttpReviewQueueClient:
    """The sole production adapter allowed to call the review-queue service."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), trust_env=False)

    def enqueue_draft(self, *, job_id: str, org_id: str, site_id: str, created_by: str,
                      function_code: str, content: str, lint_issues: list[dict[str, Any]]) -> str:
        body = {
            "job_id": job_id,
            "org_id": org_id,
            "site_id": site_id,
            "created_by": created_by,
            "function_code": function_code,
            "content": content,
            "lint_issues": lint_issues,
            "status": "draft",
        }
        try:
            with authorized_egress("review_queue"):
                response = self.client.post(
                    f"{self.base_url}/api/review/drafts",
                    headers={"Idempotency-Key": job_id},
                    json=body,
                )
        except httpx.RequestError as exc:
            raise ReviewQueueUnavailable("复核队列暂不可用") from exc
        if response.status_code >= 500 or response.status_code in {408, 429}:
            raise ReviewQueueUnavailable("复核队列暂不可用")
        if response.status_code >= 400:
            raise ReviewQueueUnavailable("复核队列拒绝请求")
        try:
            draft_id = response.json()["draft_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ReviewQueueUnavailable("复核队列响应格式无效") from exc
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise ReviewQueueUnavailable("复核队列响应格式无效")
        return draft_id
