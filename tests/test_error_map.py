from __future__ import annotations

import pytest

from app.errors import ERROR_MESSAGES, FALLBACK_ERROR_MESSAGE, GatewayFailure, user_message
from tests.conftest import MockGateway


@pytest.mark.parametrize("error_code", [
    "QUOTA_EXCEEDED",
    "SCHEMA_VALIDATION_FAILED",
    "ALL_PROVIDERS_FAILED",
    "GW_DISABLED",
])
def test_gateway_error_is_persisted_with_safe_chinese_message(build_service, user, error_code):
    service, _, _, _, _ = build_service(gateway=MockGateway(error=GatewayFailure(error_code)))

    result = service.run("copywriting", {"brief": "演示"}, user)

    assert result.status == "failed"
    assert result.error_code == error_code
    assert result.message == ERROR_MESSAGES[error_code]
    assert result.message.isascii() is False


def test_unknown_error_code_uses_chinese_fallback():
    assert user_message("UNRECOGNIZED") == FALLBACK_ERROR_MESSAGE


def test_invalid_gateway_schema_maps_to_schema_failure(build_service, user):
    service, _, _, _, _ = build_service(gateway=MockGateway(response={"unexpected": "shape"}))

    result = service.run("copywriting", {"brief": "演示"}, user)

    assert result.status == "failed"
    assert result.error_code == "SCHEMA_VALIDATION_FAILED"
    assert result.message == ERROR_MESSAGES["SCHEMA_VALIDATION_FAILED"]
