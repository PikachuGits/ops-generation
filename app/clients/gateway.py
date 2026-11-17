from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from app.egress import authorized_egress
from app.errors import GatewayFailure


class GatewayClient(Protocol):
    def generate(self, *, function_code: str, messages: list[dict[str, str]]) -> dict[str, Any]: ...


class DisabledGatewayClient:
    def generate(self, *, function_code: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        raise GatewayFailure("GW_DISABLED")


class HttpGatewayClient:
    """The sole production adapter allowed to call the model gateway."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False)

    def generate(self, *, function_code: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        body = {
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ops_generation_result",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        try:
            with authorized_egress("gateway"):
                response = self.client.post(
                    f"{self.base_url}/api/gw/v1/chat/completions",
                    headers={"X-Feature-Code": function_code},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise GatewayFailure("ALL_PROVIDERS_FAILED") from exc
        except httpx.RequestError as exc:
            raise GatewayFailure("ALL_PROVIDERS_FAILED") from exc

        if response.status_code == 429:
            raise GatewayFailure("QUOTA_EXCEEDED")
        if response.status_code >= 500:
            raise GatewayFailure("ALL_PROVIDERS_FAILED")
        if response.status_code >= 400:
            raise GatewayFailure("SCHEMA_VALIDATION_FAILED")
        try:
            payload = response.json()
            if isinstance(payload, dict) and "choices" in payload:
                raw = payload["choices"][0]["message"]["content"]
                payload = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise GatewayFailure("SCHEMA_VALIDATION_FAILED") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
            raise GatewayFailure("SCHEMA_VALIDATION_FAILED")
        return payload
