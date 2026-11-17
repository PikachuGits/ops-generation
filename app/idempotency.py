from __future__ import annotations

import hashlib
import json


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def input_digest(function_code: str, inputs: dict[str, object]) -> str:
    payload = canonical_json({"function_code": function_code, "inputs": inputs})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scoped_key(org_id: str, site_id: str, user_id: str, function_code: str, input_sha256: str) -> str:
    payload = canonical_json({
        "org_id": org_id,
        "site_id": site_id,
        "user_id": user_id,
        "function_code": function_code,
        "input_sha256": input_sha256,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
