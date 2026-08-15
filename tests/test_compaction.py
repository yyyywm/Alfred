"""compaction.py 的 model-free 结构化修剪逻辑测试。"""
from __future__ import annotations

import pytest

from alfred.compaction import (
    _PRUNE_HEAD_CHARS,
    _PRUNE_TAIL_CHARS,
    prune_tool_result,
    _extract_error_lines,
)


# ── _extract_error_lines ──────────────────────────────────────────────


def test_extract_error_lines_empty():
    assert _extract_error_lines("") == ""
    assert _extract_error_lines("all good") == ""


def test_extract_error_lines_basic_error():
    text = (
        "line1 ok\n"
        "line2 ok\n"
        "ERROR: something broke\n"
        "line3 ok\n"
        "line4 ok\n"
    )
    result = _extract_error_lines(text)
    assert "ERROR: something broke" in result


def test_extract_error_lines_traceback_capped():
    lines = ["Traceback (most recent call last):"]
    for i in range(20):
        lines.append(f"  File 'x.py', line {i}")
    lines.append("ValueError: boom")
    text = "\n".join(lines)
    result = _extract_error_lines(text)
    # 首 3 + 尾 3 = 6 行上限
    result_lines = [l for l in result.splitlines() if l.strip()]
    assert len(result_lines) <= 6


def test_extract_error_lines_lowercase_ok():
    text = "error lowercase test\n"
    assert "error lowercase test" in _extract_error_lines(text)


# ── prune_tool_result ────────────────────────────────────────────────


def test_prune_whitelist_preserved():
    content = "a" * 50_000
    trimmed, saved = prune_tool_result("code_patch", content)
    assert trimmed == content
    assert saved == 0


def test_prune_short_content_unchanged():
    content = "short output"
    trimmed, saved = prune_tool_result("notes_search", content)
    assert trimmed == content
    assert saved == 0


def test_prune_shell_structured_with_errors():
    lines = ["line_%d" % i for i in range(500)]
    lines[250] = "Traceback (most recent call last):"
    lines[251] = "  File 'x.py', line 1"
    lines[252] = "ValueError: oops"
    content = "\n".join(lines)
    trimmed, saved = prune_tool_result("shell", content)
    assert saved > 0
    assert trimmed[:_PRUNE_HEAD_CHARS] == content[:_PRUNE_HEAD_CHARS]
    assert "Traceback" in trimmed
    assert "ValueError" in trimmed


def test_prune_shell_no_errors_still_cuts():
    content = "x" * 30_000
    trimmed, saved = prune_tool_result("run_python", content)
    assert saved > 0
    assert len(trimmed) < len(content)


def test_prune_other_fallback_truncation():
    content = "y" * 10_000
    trimmed, saved = prune_tool_result("memory_search", content)
    assert saved > 0
    assert len(trimmed) <= _PRUNE_TAIL_CHARS + 50  # +50 for truncation suffix


def test_prune_empty():
    trimmed, saved = prune_tool_result("shell", "")
    assert trimmed == ""
    assert saved == 0


def test_prune_none_name_fallback():
    content = "z" * 10_000
    trimmed, saved = prune_tool_result(None, content)
    assert saved > 0