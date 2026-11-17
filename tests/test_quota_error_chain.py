from __future__ import annotations

import httpx

from app.clients.gateway import HttpGatewayClient
from app.service import UserContext


def test_http_429_marks_job_failed_with_quota_error_and_chinese_message(
    build_service, user: UserContext
):
    requests: list[httpx.Request] = []

    def rate_limited_response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, json={"error": "quota exceeded"})

    with httpx.Client(transport=httpx.MockTransport(rate_limited_response)) as http_client:
        gateway = HttpGatewayClient("https://gateway.test", http_client)
        service, _, review, _, _ = build_service(gateway=gateway)

        result = service.run("copywriting", {"brief": "虚构验收样例"}, user)

        assert result.status == "failed"
        assert result.error_code == "QUOTA_EXCEEDED"
        assert result.message == "模型额度已用尽，请稍后重试或联系管理员"
        assert len(requests) == 1
        assert requests[0].url.path == "/api/gw/v1/chat/completions"
        assert review.calls == 0
