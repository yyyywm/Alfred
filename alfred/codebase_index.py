"""代码库活索引：agent 定位自身代码模块的地图。

设计依据：operator-memory 的 Project Index——代码库导航与知识路由是不同
关注点，索引带"何时读"条件，主索引常驻、按需精读，避免每轮重新探索目录
结构烧 token。Alfred 的 code_patch 自举进化需要理解自身代码库，之前靠
AGENTS.md + 临时探索。

与 operator 的差异：operator 的索引由 agent 手写维护；Alfred 的 agent 没有
通用文件写工具，索引由宿主侧机械生成（ast 提取模块 docstring 首行），
按源文件 mtime 失效重建——零 LLM、确定性、永不过期。
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from .config import PROJECT_ROOT, Config

INDEX_FILENAME = "codebase_index.md"


def index_path(config: Config) -> Path:
    return config.path("data") / INDEX_FILENAME


def _module_summary(path: Path) -> str:
    """模块 docstring 首行；没有 docstring 或解析失败返回空串。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _iter_modules(pkg_root: Path) -> list[Path]:
    return sorted(
        p for p in pkg_root.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def generate_index(pkg_root: Path | None = None) -> str:
    """生成代码库索引文本：每个模块一行「路径 — docstring 首行」。"""
    root = pkg_root or (PROJECT_ROOT / "alfred")
    lines = [
        "# 代码库索引（宿主自动生成，按源码 mtime 失效重建）",
        "",
        "查看或修改管家自身源代码时的定位地图：先从这里找到负责的模块，"
        "再用 file_read 精读，不要逐文件漫游。",
        "",
    ]
    base = root.parent
    for p in _iter_modules(root):
        rel = p.relative_to(base).as_posix()
        summary = _module_summary(p)
        lines.append(f"- {rel}" + (f" — {summary}" if summary else ""))
    return "\n".join(lines)


def _is_stale(pkg_root: Path, out: Path) -> bool:
    if not out.is_file():
        return True
    index_mtime = out.stat().st_mtime
    return any(p.stat().st_mtime > index_mtime for p in _iter_modules(pkg_root))


def ensure_index(config: Config, pkg_root: Path | None = None) -> str:
    """返回最新索引文本：缺失或源码比索引新时重建。

    索引失败返回空串（不注入），绝不阻塞 chat 启动。
    """
    try:
        pkg_root = pkg_root or (PROJECT_ROOT / "alfred")
        out = index_path(config)
        if _is_stale(pkg_root, out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(generate_index(pkg_root), encoding="utf-8")
        return out.read_text(encoding="utf-8")
    except Exception as exc:
        logging.getLogger(__name__).warning("代码库索引生成失败：%s", exc)
        return ""
