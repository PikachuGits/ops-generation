from __future__ import annotations

from app.linting import lint_content


def test_lint_detects_unclosed_markdown_fence_as_structural_issue():
    issues = lint_content("运行方式：\n```python\nprint('demo')")
    assert any(issue["code"] == "STRUCTURE_INCOMPLETE" for issue in issues)


def test_lint_does_not_flag_balanced_markdown_fence():
    issues = lint_content("运行方式：\n```python\nprint('demo')\n```")
    assert not any(issue["code"] == "STRUCTURE_INCOMPLETE" for issue in issues)
