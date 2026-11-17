from __future__ import annotations


def test_api_requires_trusted_tenant_context(api_request):
    response = api_request(
        "POST",
        "/api/v1/generations",
        json_body={"function_code": "copywriting", "inputs": {"brief": "演示"}},
    )
    assert response.status_code == 401


def test_api_empty_inputs_returns_422(api_request):
    response = api_request(
        "POST",
        "/api/v1/generations",
        headers={"X-Org-ID": "org", "X-Site-ID": "site", "X-User-ID": "user"},
        json_body={"function_code": "copywriting", "inputs": {}},
    )
    assert response.status_code == 422
