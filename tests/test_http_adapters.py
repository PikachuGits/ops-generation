from __future__ import annotations

import httpx
import pytest

from app.clients.gateway import HttpGatewayClient
from app.clients.review_queue import HttpReviewQueueClient
from app.errors import GatewayFailure, ReviewQueueUnavailable


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (429, {"error": "limited"}, "QUOTA_EXCEEDED"),
        (503, {"error": "down"}, "ALL_PROVIDERS_FAILED"),
        (200, {"unexpected": "schema"}, "SCHEMA_VALIDATION_FAILED"),
        (200, {"choices": [{"message": {"content": "not json"}}]}, "SCHEMA_VALIDATION_FAILED"),
    ],
)
def test_gateway_http_responses_map_to_required_codes(status_code, body, expected):
    seen = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: (seen.append(request), httpx.Response(status_code, json=body))[1])
    )
    gateway = HttpGatewayClient("https://gateway.test", client)

    with pytest.raises(GatewayFailure) as exc_info:
        gateway.generate(function_code="copywriting", messages=[{"role": "user", "content": "虚构样例"}])

    assert exc_info.value.error_code == expected
    request = seen[0]
    assert request.url.path == "/api/gw/v1/chat/completions"
    assert request.headers["X-Feature-Code"] == "copywriting"


def test_gateway_http_openai_response_shape_is_supported():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "{\"content\": \"已生成\"}"}}]})
    ))
    result = HttpGatewayClient("https://gateway.test", client).generate(
        function_code="copywriting", messages=[{"role": "user", "content": "虚构样例"}]
    )
    assert result == {"content": "已生成"}


@pytest.mark.parametrize("status_code", [503, 429, 408])
def test_review_queue_temporary_errors_remain_retryable(status_code):
    seen = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: (seen.append(request), httpx.Response(status_code, json={"error": "down"}))[1])
    )
    queue = HttpReviewQueueClient("https://review.test", client)

    with pytest.raises(ReviewQueueUnavailable):
        queue.enqueue_draft(
            job_id="job-1", org_id="org", site_id="site", created_by="user",
            function_code="copywriting", content="虚构草稿", lint_issues=[],
        )

    assert seen[0].headers["Idempotency-Key"] == "job-1"
    assert seen[0].url.path == "/api/review/drafts"
