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


def test_user_facts_header_reused_and_greedy_pack(tmp_path):
    """自动沉淀章节头只出现一次；空间不足时贪心装入能装下的，不整批放弃。

    回归：之前每次复盘都追加一个重复的 ## 标题，且接近上限时整批放弃。
    """
    from alfred.memory.consolidate import _apply_user_facts_to_human

    cfg = Config(memory={
        "dir": str(tmp_path / "mem"),
        "human_block_char_limit": 400,  # 模板 251 字符，留约 100 字符余量
    })
    blocks = MemoryBlocks(cfg)

    _apply_user_facts_to_human(cfg, ["用户喜欢喝茶", "用户养了一只猫"])
    human = blocks.read("human")
    assert human.count("自动沉淀的用户事实") == 1
    for fact in ("用户喜欢喝茶", "用户养了一只猫"):
        assert fact in human

    # 第二次复盘写入新事实时复用同一章节头（回归：旧实现每次追加一个新标题）
    _apply_user_facts_to_human(cfg, ["用户住在杭州"])
    human = blocks.read("human")
    assert human.count("自动沉淀的用户事实") == 1
    assert "用户住在杭州" in human

    # 幂等：已存在的事实不重复写入
    assert _apply_user_facts_to_human(cfg, ["用户喜欢喝茶"]) == []
    assert blocks.read("human").count("用户喜欢喝茶") == 1

    # 空间耗尽时返回跳过提示，而不是静默丢弃
    too_big = ["这是一条非常长的用户事实。" * 30]
    result = _apply_user_facts_to_human(cfg, too_big)
    assert result and result[0].startswith("[跳过:")
    assert blocks.read("human") == human  # 未写入任何东西


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
    }, paths={"history_dir": str(tmp_path / "hist_large")})
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

    hist_dir = cfg.path(cfg.paths.history_dir)
    pending_path = hist_dir / "consolidate_pending.jsonl"
    assert pending_path.exists(), "大改动 human 更新应被暂存"
    records = [json.loads(line) for line in pending_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["drafts"].get("human_block_update") == big_update


def test_apply_unattended_shrink_human_update_pending(tmp_path, monkeypatch):
    """human_block_update 大幅删减时也应降级 pending（防止 LLM 绕过保护）。

    场景：已有 1000 字画像，LLM 想重写为 200 字摘要——delta 为负但绝对值大，
    应走待审而非自动写入。
    """
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
    }, paths={"history_dir": str(tmp_path / "hist_shrink")})
    blocks = MemoryBlocks(cfg)
    # 填入约 1000 字"真实内容"
    blocks.update("human", "# Human Block\n## 基本资料\n" + "真实内容 " * 120, reason="seed")

    # 大幅缩减为短摘要（delta ≈ -780，abs > 500）
    short_update = "## 基本资料\n短"
    drafts = {
        "lessons": [],
        "memory_entries": [],
        "human_block_update": short_update,
        "episodes": [],
    }
    apply_unattended(cfg, drafts)

    hist_dir = cfg.path(cfg.paths.history_dir)
    pending_path = hist_dir / "consolidate_pending.jsonl"
    assert pending_path.exists(), "大幅删减也应降级 pending"
    records = [json.loads(line) for line in pending_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["drafts"].get("human_block_update") == short_update


# ── P1: schedule_fire_pending 跨 session ──────────────────────────────

def test_schedule_fire_pending_fires_and_marks(tmp_path):
    """到期任务应返回 prompt 并标记为 fired。"""
    from alfred.schedule import schedule_create, schedule_fire_pending

    cfg = Config(paths={"history_dir": str(tmp_path / "hist")})

    result = schedule_create(cfg, "sess1", "task", "fire me!",
                             due_at=0.0)
    assert result["ok"]
    assert "已创建" in result["message"]

    prompts = schedule_fire_pending(cfg)
    assert prompts == ["fire me!"]

    assert schedule_fire_pending(cfg) == []


def test_schedule_fire_pending_ignores_future(tmp_path):
    """未到期的任务不应被 fire。"""
    import time
    from alfred.schedule import schedule_create, schedule_fire_pending

    cfg = Config(paths={"history_dir": str(tmp_path / "hist")})
    future = time.time() + 86400 * 7

    schedule_create(cfg, "sess1", "future task", "future prompt",
                    due_at=future)
    assert schedule_fire_pending(cfg) == []
