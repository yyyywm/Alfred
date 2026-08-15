"""goals.py / schedule.py 单元测试。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from alfred.config import Config
from alfred.goals import GoalStatus, create_goal, get_goal, update_goal
from alfred.schedule import schedule_create, schedule_delete, schedule_list, schedule_fire_pending


@pytest.fixture()
def tmp_config(tmp_path: Path):
    cfg = Config()
    cfg.paths.history_dir = str(tmp_path / "history")
    (tmp_path / "history").mkdir(parents=True)
    return cfg


class TestGoalStatus:
    def test_valid_statuses(self):
        assert GoalStatus.active == "active"
        assert "active" in GoalStatus.VALID
        assert "bogus" not in GoalStatus.VALID

    def test_unknown_status_rejected_when_goal_exists(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        r = update_goal(tmp_config, "s1", status="bogus")
        assert not r["ok"]
        assert "无效状态" in r["message"]


class TestGoalCreate:
    def test_create_new(self, tmp_config):
        r = create_goal(tmp_config, "s1", "读这本书")
        assert r["ok"]
        assert r["status"] == GoalStatus.active
        assert "读这本书" in r["message"]

    def test_update_existing(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        r = create_goal(tmp_config, "s1", "读完并写笔记")
        assert r["ok"]
        assert r["status"] == GoalStatus.active
        assert "读完并写笔记" in r["message"]

    def test_persists_to_file(self, tmp_config):
        create_goal(tmp_config, "s1", "测试")
        p = tmp_config.path(tmp_config.paths.history_dir) / "goals" / "s1.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["description"] == "测试"


class TestGoalUpdate:
    def test_no_goal_error(self, tmp_config):
        r = update_goal(tmp_config, "s1", progress="开始读")
        assert not r["ok"]
        assert "没有活跃目标" in r["message"]

    def test_update_progress(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        r = update_goal(tmp_config, "s1", progress="读完第 3 章")
        assert r["ok"]
        assert r["changes"] == "progress"
        state = get_goal(tmp_config, "s1")
        assert state["progress"] == "读完第 3 章"

    def test_block(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        r = update_goal(tmp_config, "s1", block_reason="模型不可用")
        assert r["ok"]
        state = get_goal(tmp_config, "s1")
        assert state["status"] == GoalStatus.blocked

    def test_complete(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        r = update_goal(tmp_config, "s1", status="completed")
        assert r["ok"]
        state = get_goal(tmp_config, "s1")
        assert state["status"] == GoalStatus.completed

    def test_no_changes(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        r = update_goal(tmp_config, "s1")
        assert r["ok"]
        assert r["message"] == "无变更。"


class TestGoalGet:
    def test_no_goal_returns_none(self, tmp_config):
        assert get_goal(tmp_config, "s1") is None

    def test_full_state(self, tmp_config):
        create_goal(tmp_config, "s1", "读这本书")
        update_goal(tmp_config, "s1", progress="第 3 章")
        state = get_goal(tmp_config, "s1")
        assert state is not None
        assert state["description"] == "读这本书"
        assert state["progress"] == "第 3 章"


class TestSchedule:
    def test_create_default_due(self, tmp_config):
        r = schedule_create(tmp_config, "s1", "每周复盘", "提示用户复盘", due_at=None)
        assert r["ok"]
        assert "id" in r

    def test_create_with_due(self, tmp_config):
        r = schedule_create(
            tmp_config, "s1", "2 天后提醒", "该复盘了",
            due_at=1760000000.0,
        )
        assert r["ok"]

    def test_list(self, tmp_config):
        schedule_create(tmp_config, "s1", "A", "p")
        schedule_create(tmp_config, "s2", "B", "p")
        r = schedule_list(tmp_config, "s1")
        assert r["count"] >= 1
        assert any("A" in e["description"] for e in r["entries"])

    def test_list_filter_session(self, tmp_config):
        schedule_create(tmp_config, "s1", "A", "p")
        schedule_create(tmp_config, "s2", "B", "p")
        r = schedule_list(tmp_config, "s1")
        assert r["count"] == 1

    def test_delete(self, tmp_config):
        r = schedule_create(tmp_config, "s1", "A", "p")
        r2 = schedule_delete(tmp_config, r["id"])
        assert r2["ok"]
        assert r2["cancelled"] == 1

    def test_delete_not_found(self, tmp_config):
        r = schedule_delete(tmp_config, "xxxx")
        assert not r["ok"]

    def test_fire_pending(self, tmp_config):
        r = schedule_create(tmp_config, "s1", "A", "该复盘了", due_at=0.0)
        fired = schedule_fire_pending(tmp_config)
        assert "该复盘了" in fired
        # 第二次调用不应重复触发
        fired2 = schedule_fire_pending(tmp_config)
        assert fired2 == []