"""Self-modification: code_patch 工具的实现模块。

理论依据（arXiv 溯源）：
- CodeAct (Wang et al., ICML 2024, 2402.01030)：可执行代码作为统一动作空间，
  本工具是 CodeAct 在源码修改场景的具体化。
- SWE-bench (Jimenez et al., 2023) 评估范式：生成 patch → 测试验证 → 通过才算修复。
  本工具的三重门禁（路径/语法/测试）对齐该标准流程。
- 自修改自身代码（而非修改第三方项目）在学术界是开放问题，尚无成熟基准。
  因此强约束：人类是进化方向决策者，agent 是执行者。

三重门禁：
1. 路径门禁：只允许写入 alfred/ 和 config.yaml（tests/ 不在范围内）
2. 语法门禁：Python 文件用 py_compile 验证
3. 测试门禁：修改后跑 pytest tests/ -q，不过则回滚

agent 端用法：
    code_patch(path="alfred/x.py", old_string="...要替换的片段...",
               new_string="...新内容...")
"""

from __future__ import annotations

import os
import py_compile
import subprocess
import sys
from pathlib import Path

from .config import load_config  # noqa: F401 — 保留导入供未来扩展（路径配置集中化管理）


# 允许写入的顶层路径（相对项目根）。tests/ 被排除——
# 允许 agent 修改测试文件会降低测试门禁的可信度。
_ALLOWED_PREFIXES = (
    "alfred/",
)

# 允许写入的精确文件名（相对项目根）
_ALLOWED_FILES = {
    "config.yaml",
}

# 禁止写入的文件模式
_FORBIDDEN_SUFFIXES = (".pyc",)

# 测试命令（在 agent 自己的 Python 环境里跑）
_TEST_CMD = ["-m", "pytest", "tests/", "-q", "--tb=no", "-x"]


def _project_root() -> Path:
    """定位项目根目录：alfred 包所在目录的父目录。"""
    return Path(__file__).resolve().parent.parent


def _resolve_target(relative_path: str) -> Path:
    """解析并校验目标路径。返回绝对路径，违规则抛 ValueError。"""
    root = _project_root()
    rel = Path(relative_path)
    if rel.is_absolute():
        raise ValueError(f"不允许使用绝对路径：{rel}")
    if rel.parts and (rel.parts[0] == ".." or rel.parent == Path("..")):
        raise ValueError(f"不允许使用上级路径：{rel}")
    # 规范化后与 root 比较
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"路径逃逸到项目根之外：{target}")

    rel_str = target.relative_to(root).as_posix()
    if any(rel_str.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES):
        raise ValueError(f"不允许写入的文件类型：{rel_str}")

    allowed = (
        rel_str in _ALLOWED_FILES
        or any(rel_str.startswith(pfx) for pfx in _ALLOWED_PREFIXES)
    )
    if not allowed:
        raise ValueError(
            f"路径不在允许范围内：{rel_str}"
            f"（允许：{', '.join(sorted(_ALLOWED_FILES))} 或 "
            f"{', '.join(pfx.rstrip('/') + '/' for pfx in _ALLOWED_PREFIXES)}）"
        )

    return target


def _validate_syntax(target: Path) -> None:
    """Python 文件才需要语法验证。"""
    if target.suffix != ".py":
        return
    try:
        py_compile.compile(str(target), doraise=True)
    except py_compile.PyCompileError as exc:
        raise ValueError(f"语法检查失败：{exc}") from None


def _run_tests() -> tuple[bool, str]:
    """在项目根目录跑 pytest。返回 (success, output_tail)。"""
    root = _project_root()
    try:
        proc = subprocess.run(
            [sys.executable, *_TEST_CMD],
            capture_output=True, text=True, timeout=120, cwd=root,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout + proc.stderr)[-3000:]
        return ok, tail
    except subprocess.TimeoutExpired:
        return False, "测试超时（120 秒限制）"


def code_patch(relative_path: str, old_string: str, new_string: str) -> str:
    """应用一个精确的文本替换到目标文件，三重门禁。

    1. 路径门禁：校验相对路径在允许范围内
    2. 语法门禁：Python 文件修改后用 py_compile 验证
    3. 测试门禁：修改后跑 pytest，不过则回滚到原文件

    返回操作结果描述。
    """
    # ── ① 路径门禁 ──────────────────────────────────────────────
    try:
        target = _resolve_target(relative_path)
    except ValueError as exc:
        return f"路径校验失败：{exc}"

    if not target.is_file():
        return f"目标文件不存在：{relative_path}"

    original = target.read_text(encoding="utf-8")

    # 唯一性检查：old_string 必须出现且恰好一次
    count = original.count(old_string)
    if count == 0:
        return (
            f"未找到待替换的文本（old_string 出现 0 次）。"
            f"文件内容片段预览：{original[:200]!r}..."
        )
    if count > 1:
        return (
            f"old_string 不唯一（出现 {count} 次）。"
            "请提供更多上下文使其唯一。"
        )

    # ── ② 应用替换 ─────────────────────────────────────────────
    modified = original.replace(old_string, new_string)
    target.write_text(modified, encoding="utf-8")

    # ── 语法门禁 ──────────────────────────────────────────────
    try:
        _validate_syntax(target)
    except ValueError as exc:
        # 回滚
        target.write_text(original, encoding="utf-8")
        return f"语法门禁失败：{exc}"

    # ── ③ 测试门禁 ──────────────────────────────────────────────
    ok, tail = _run_tests()
    if not ok:
        # 回滚
        target.write_text(original, encoding="utf-8")
        return (
            f"测试门禁失败：pytest 未通过。"
            f"已将 {relative_path} 回滚到修改前。"
            f"pytest 输出（尾部）：\n{tail}"
        )

    return (
        f"已应用补丁：{relative_path}"
        f"（{len(old_string)} → {len(new_string)} 字符）"
        f"；语法检查通过；pytest 通过。"
    )