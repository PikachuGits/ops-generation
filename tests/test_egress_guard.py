from __future__ import annotations

import json
import socket
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.clients.gateway import HttpGatewayClient
from app.clients.review_queue import HttpReviewQueueClient
from app.config import Settings
from app.egress import active_egress_client
from app.service import OpsGenerationService
from app.queueing import NoopDispatcher


def test_only_gateway_and_review_queue_http_clients_can_make_outbound_calls(
    monkeypatch, session_factory: sessionmaker, user
):
    gateway_calls: list[str] = []
    review_calls: list[str] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_calls.append(str(request.url))
        return httpx.Response(200, json={"content": "合规演示文案"})

    def review_handler(request: httpx.Request) -> httpx.Response:
        review_calls.append(str(request.url))
        return httpx.Response(201, json={"draft_id": "draft-egress"})

    gateway_http = httpx.Client(transport=httpx.MockTransport(gateway_handler))
    review_http = httpx.Client(transport=httpx.MockTransport(review_handler))
    gateway = HttpGatewayClient("https://gateway.test", gateway_http)
    review = HttpReviewQueueClient("https://review.test", review_http)
    service = OpsGenerationService(
        session_factory=session_factory,
        gateway=gateway,
        review_queue=review,
        redis_client=None,
        dispatcher=NoopDispatcher(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:", max_input_chars=12000),
    )

    original_request = httpx.Client.request

    def guarded_request(self, method, url, *args, **kwargs):
        client_name = active_egress_client()
        assert client_name in {"gateway", "review_queue"}, f"未授权 HTTP 出站: {url}"
        parsed = urlparse(str(url))
        if client_name == "gateway":
            assert parsed.hostname == "gateway.test"
            assert parsed.path == "/api/gw/v1/chat/completions"
        else:
            assert parsed.hostname == "review.test"
            assert parsed.path == "/api/review/drafts"
        return original_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", guarded_request)
    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        assert active_egress_client() in {"gateway", "review_queue"}, f"未授权 socket 出站: {address}"
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    result = service.run("copywriting", {"brief": "演示"}, user)
    assert result.status == "drafted"
    assert gateway_calls == ["https://gateway.test/api/gw/v1/chat/completions"]
    assert review_calls == ["https://review.test/api/review/drafts"]

    with httpx.Client() as unapproved:
        with pytest.raises(AssertionError, match="未授权 HTTP 出站"):
            unapproved.get("https://not-allowlisted.test/path")
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError, match="未授权 socket 出站"):
            sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()
