from __future__ import annotations

from typing import Any

DISALLOWED_PHRASES = ("保证收益", "绝对有效", "稳赚")
MAX_OUTPUT_CHARS = 20000


def lint_content(content: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if len(content) > MAX_OUTPUT_CHARS:
        issues.append({"code": "OUTPUT_TOO_LONG", "message": "生成内容超过建议长度，请人工复核"})
    if not content.strip():
        issues.append({"code": "EMPTY_CONTENT", "message": "生成内容为空，请人工复核"})
    if content.count("```") % 2:
        issues.append({"code": "STRUCTURE_INCOMPLETE", "message": "Markdown 代码块未闭合，请人工复核"})
    for phrase in DISALLOWED_PHRASES:
        if phrase in content:
            issues.append({"code": "DISALLOWED_PHRASE", "phrase": phrase, "message": "发现需人工复核的表述"})
    return issues
