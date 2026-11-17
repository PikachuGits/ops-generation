from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from app.errors import RequestInputError


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    function_code: str
    version: str
    execution_mode: Literal["sync", "async"]
    required_fields: tuple[str, ...]
    max_chars: int
    instruction: str

    def render(self, inputs: dict[str, Any]) -> list[dict[str, str]]:
        payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return [
            {"role": "system", "content": self.instruction},
            {"role": "user", "content": f"请仅根据以下虚构演示输入生成结果，并以 JSON 返回。\n{payload}"},
        ]


# These are replaceable demo contracts, not customer-provided function codes or schemas.
TEMPLATES: dict[str, PromptTemplate] = {
    "copywriting": PromptTemplate(
        function_code="copywriting",
        version="demo-v1",
        execution_mode="sync",
        required_fields=("brief",),
        max_chars=12000,
        instruction="根据 brief 生成一份中文运营文案。返回 JSON 对象，且 content 为字符串。",
    ),
    "proposal": PromptTemplate(
        function_code="proposal",
        version="demo-v1",
        execution_mode="async",
        required_fields=("project_scope",),
        max_chars=12000,
        instruction="根据 project_scope 生成一份中文方案草稿。返回 JSON 对象，且 content 为字符串。",
    ),
    "meeting_summary": PromptTemplate(
        function_code="meeting_summary",
        version="demo-v1",
        execution_mode="async",
        required_fields=("transcript",),
        max_chars=12000,
        instruction="根据 transcript 生成中文会后总结草稿。返回 JSON 对象，且 content 为字符串。",
    ),
}


def get_template(function_code: str) -> PromptTemplate:
    try:
        return TEMPLATES[function_code]
    except KeyError as exc:
        raise RequestInputError("不支持的 function_code") from exc


def validate_required_fields(template: PromptTemplate, inputs: dict[str, Any]) -> None:
    missing = [name for name in template.required_fields if name not in inputs or inputs[name] in (None, "")]
    if missing:
        raise RequestInputError("缺少必填输入项")
