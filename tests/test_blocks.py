"""memory blocks 测试：读写、字符上限、git 版本化。"""

import pytest

from alfred.config import Config
from alfred.memory.blocks import MemoryBlocks


@pytest.fixture
def blocks(tmp_path):
    cfg = Config(memory={"dir": str(tmp_path / "mem"), "block_char_limit": 500})
    return MemoryBlocks(cfg)


def test_templates_created(blocks):
    assert "persona" in blocks.read("persona").lower() or "人格" in blocks.read("persona")
    assert blocks.read("human")


def test_update_and_commit(blocks):
    result = blocks.update("human", "用户喜欢直接了当的沟通。", reason="测试")
    assert "已更新" in result
    assert blocks.read("human") == "用户喜欢直接了当的沟通。"
    history = blocks.history("human")
    assert any("测试" in h for h in history)


def test_char_limit_rejected(blocks):
    result = blocks.update("human", "x" * 501)
    assert "被拒绝" in result
    assert "上限" in result


def test_no_change_skipped(blocks):
    content = blocks.read("human")
    result = blocks.update("human", content)
    assert "无变化" in result


def test_unknown_block_rejected(blocks):
    with pytest.raises(ValueError):
        blocks.read("nonexistent")


def test_rollback(blocks):
    blocks.update("human", "版本A")
    blocks.update("human", "版本B")
    commits = blocks.history("human")
    # 时间倒序：[版本B 提交, 版本A 提交, init]——取版本A 的提交
    assert len(commits) >= 3
    sha = commits[1].split()[0]
    blocks.rollback("human", sha)
    assert blocks.read("human") == "版本A"
