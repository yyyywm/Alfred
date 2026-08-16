"""新增功能测试：per-block limit / auto-consolidate 自动写入 / save_episode 工具 / schedule 跨 session"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from alfred.config import Config
from alfred.memory.blocks import MemoryBlocks
from alfred.memory.consolidate import apply_unattended
from alfred.memory.consolidate_state import AUTO_HUMAN_UPDATE_MAX_CHARS


# ── P0-1: per-block limit ────────────────────────────────────────────

def test_human_block_independent_limit(tmp_path):
    cfg = Config(memory={
        "dir": str(tmp_path / "mem"),
        "block_char_limit": 2000,
        "human_block_char_limit": 500,
    })
    blocks = MemoryBlocks(cfg)
    assert blocks.limit_for("human") == 500
    assert blocks.limit_for("persona") == 2000  # 回退全局默认

    content = "A" * 300
    result = blocks.update("human", content, reason="test")
    assert "已更新" in result

    over = "A" * 501
    result = blocks.update("human", over, reason="test")
    assert "被拒绝" in result


def test_human_template_is_structured(tmp_path):
    cfg = Config(memory={"dir": str(tmp_path / "mem")})
    blocks = MemoryBlocks(cfg)
    text = blocks.read("human")
    assert "## 基本资料" in text
    assert "## 性格与思维" in text
    assert "## 关键决策记录" in text


def test_global_limit_fallback(tmp_path):
    """未设置块级上限时回退全局 block_char_limit。"""
    cfg = Config(memory={"dir": str(tmp_path / "mem"), "block_char_limit": 1500})
    blocks = MemoryBlocks(cfg)
    assert blocks.limit_for("human") == 1500
    assert blocks.limit_for("persona") == 1500


# ── P0-2: auto-consolidate 自动写入 ──────────────────────────────────

def test_apply_unattended_writes_memory_and_lessons_and_episodes(tmp_path, monkeypatch):
    """apply_unattended 在无人值守模式下应自动写入 lessons / memory_entries / episodes。"""
    # mock longterm
    class MockMemory:
        def __init__(self):
            self.added = []
        def add(self, msgs, user_id):
            self.added.extend(msgs)

    _mock_mem = MockMemory()
    _mock_eps = []

    def fake_get_memory(cfg):
        return _mock_mem

    def fake_save_ep(cfg, ep):
        _mock_eps.append({
            "situation": ep.situation,
            "result": ep.result,
        })
        return "ep123"

    from alfred.memory import consolidate, episodic, longterm

    monkeypatch.setattr(longterm, "get_memory", fake_get_memory)
    monkeypatch.setattr(episodic, "save_episode", fake_save_ep)

    cfg = Config(memory={"dir": str(tmp_path / "mem")})
    drafts = {
        "lessons": [{"category": "test", "lesson": "a lesson", "context": "ctx"}],
        "memory_entries": ["user likes tea", "user lives in beijing"],
        "episodes": [
            {"situation": "S1", "thoughts": "T1", "action": "A1", "result": "R1"},
        ],
        "human_block_update": None,
    }
    applied = apply_unattended(cfg, drafts)

    # lessons 自动写入
    assert any("RefleXion" in a for a in applied)
    # memory_entries 自动写入
    assert any("记忆条目" in a for a in applied)
    assert len(_mock_mem.added) == 2
    # episodes 自动写入
    assert any("情景记忆" in a for a in applied)
    assert len(_mock_eps) == 1
    assert _mock_eps[0]["situation"] == "S1"


def test_apply_unattended_large_human_update_pending(tmp_path, monkeypatch):
    """human_block_update 改动过大时降级为 pending，不自动写入。"""
    class MockMemory:
        def add(self, msgs, user_id):
            pass

    def fake_get_memory(cfg):
        return MockMemory()

    from alfred.memory import longterm
    monkeypatch.setattr(longterm, "get_memory", fake_get_memory)

    cfg = Config(memory={
        "dir": str(tmp_path / "mem"),
        "block_char_limit": 5000,
        "human_block_char_limit": 5000,
    })
    blocks = MemoryBlocks(cfg)
    blocks.update("human", "# Human Block\n## 基本资料\n小明", reason="seed")

    big_update = "## 基本资料\n" + "X" * 1000
    drafts = {
        "lessons": [],
        "memory_entries": [],
        "human_block_update": big_update,
        "episodes": [],
    }
    apply_unattended(cfg, drafts)

    # 改动 > AUTO_HUMAN_UPDATE_MAX_CHARS，应降级 pending
    pending_path = tmp_path / "mem_history" / "consolidate_pending.jsonl"
    # 需要找到 history_dir 的实际路径
    hist_dir = cfg.path(cfg.paths.history_dir)
    pending_path = hist_dir / "consolidate_pending.jsonl"
    assert pending_path.exists(), "大改动 human 更新应被暂存"
    records = [json.loads(line) for line in pending_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["drafts"].get("human_block_update") == big_update
