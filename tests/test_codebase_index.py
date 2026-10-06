"""代码库活索引测试（纯逻辑，不调 LLM）。

设计依据：operator-memory 的 Project Index——索引是代码库的确定性地图，
Alfred 版本由宿主按源码 mtime 机械重建（agent 无通用文件写工具）。
"""

import os
import time

from alfred import codebase_index
from alfred.config import Config


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_generate_index_extracts_docstring_first_line(tmp_path):
    pkg = tmp_path / "pkg"
    _write(pkg / "core.py", '"""核心模块：负责一切。\n\n更多细节。"""\nx = 1\n')
    _write(pkg / "sub" / "util.py", '"""工具集合。"""\n')
    _write(pkg / "nodoc.py", "y = 2\n")
    _write(pkg / "__pycache__" / "cached.py", '"""不该出现。"""\n')

    text = codebase_index.generate_index(pkg)

    assert "- pkg/core.py — 核心模块：负责一切。" in text
    assert "- pkg/sub/util.py — 工具集合。" in text
    assert "- pkg/nodoc.py\n" in text or text.rstrip().endswith("- pkg/nodoc.py")
    assert "cached.py" not in text  # __pycache__ 跳过
    assert "file_read" in text  # 头部含用法说明


def test_generate_index_tolerates_syntax_error(tmp_path):
    """单个文件解析失败不影响整体索引。"""
    pkg = tmp_path / "pkg"
    _write(pkg / "good.py", '"""好模块。"""\n')
    _write(pkg / "bad.py", "def broken(:\n")

    text = codebase_index.generate_index(pkg)
    assert "好模块" in text
    assert "- pkg/bad.py" in text  # 列出但无摘要


def test_ensure_index_creates_then_reuses(tmp_path, monkeypatch):
    """缺失时生成；源码没变时复用（不重写）；源码更新后重建。"""
    pkg = tmp_path / "pkg"
    target = tmp_path / "data" / "codebase_index.md"
    _write(pkg / "a.py", '"""模块A。"""\n')

    cfg = Config()
    monkeypatch.setattr(codebase_index, "index_path", lambda c: target)

    text1 = codebase_index.ensure_index(cfg, pkg_root=pkg)
    assert "模块A" in text1
    assert target.is_file()

    # 新鲜：内容一致且文件未被重写（mtime 不变）
    mtime1 = target.stat().st_mtime
    text2 = codebase_index.ensure_index(cfg, pkg_root=pkg)
    assert text2 == text1
    assert target.stat().st_mtime == mtime1

    # 让索引看起来比源码旧：把索引 mtime 拨回过去再改源码
    past = time.time() - 100
    os.utime(target, (past, past))
    _write(pkg / "b.py", '"""模块B。"""\n')

    text3 = codebase_index.ensure_index(cfg, pkg_root=pkg)
    assert "模块B" in text3


def test_ensure_index_failure_returns_empty(tmp_path, monkeypatch):
    """索引生成失败返回空串（不注入），不阻塞 chat 启动。"""
    cfg = Config()
    monkeypatch.setattr(
        codebase_index, "index_path",
        lambda c: (_ for _ in ()).throw(RuntimeError("disk gone")),
    )
    assert codebase_index.ensure_index(cfg, pkg_root=tmp_path) == ""
