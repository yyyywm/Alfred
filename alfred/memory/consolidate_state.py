"""整理状态追踪：记录 chat 对话轮数，检测是否需要自动触发 consolidate。

设计：append-only JSONL，每次 chat turn 结束追加一行。
consolidate 完成时追加一条 completed 记录，避免重复处理同一批对话。

字段：
- ts: 时间戳
- event: "turn" | "consolidate"
- session_id: 会话 id（仅 turn 事件）
- turn_count: 当前会话总轮数
- days_since_last: 距上次 consolidate 的天数（仅 turn 事件）
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

AUTO_CONSOLIDATE_MIN_TURNS = 3
AUTO_CONSOLIDATE_MIN_HOURS = 24

# 无人值守模式下 human 块自动更新的阈值：新增内容超过此字符数时，
# 视为"改动较大"，降级为 pending 待审而非直接写入（避免漂移）。
AUTO_HUMAN_UPDATE_MAX_CHARS = 500


def _state_path(config) -> Path:
    return config.path(config.paths.history_dir) / "consolidate_state.jsonl"


def record_turn(config, session_id: str, turn_count: int) -> None:
    """记录一轮对话完成。"""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    last = get_last_consolidate_time(config)
    days_since = (datetime.now().timestamp() - last) / 86400 if last else 999
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now().timestamp(),
            "event": "turn",
            "session_id": session_id,
            "turn_count": turn_count,
            "days_since_last_consolidate": round(days_since, 2),
        }) + "\n")


def record_consolidate(config) -> None:
    """标记一次 consolidate 已完成。"""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now().timestamp(),
            "event": "consolidate",
        }) + "\n")


def get_last_consolidate_time(config) -> float | None:
    """返回最近一次 consolidate 完成的时间戳，未执行过则 None。"""
    path = _state_path(config)
    if not path.exists():
        return None
    last: float | None = None
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("event") == "consolidate":
            last = rec["ts"]
    return last


def should_auto_consolidate(config, current_turns: int) -> bool:
    """判断是否应该自动触发 consolidate。

    条件：当前会话累计轮数 ≥ MIN_TURNS，且距上次 consolidate ≥ MIN_HOURS。
    返回 True 时，调用方应在事后调用 record_consolidate() 重置计时器。
    """
    if current_turns < AUTO_CONSOLIDATE_MIN_TURNS:
        return False
    last = get_last_consolidate_time(config)
    if last is None:
        return True
    hours_since = (datetime.now().timestamp() - last) / 3600
    return hours_since >= AUTO_CONSOLIDATE_MIN_HOURS