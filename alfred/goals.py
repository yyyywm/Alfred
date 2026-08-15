"""对话中的目标状态（goal）：让 agent 感知自己正在做什么。

设计依据：
- DeepSeek-Harness goal package：event-sourced goal 状态机
- Alfred 是连续对话型管家，目标跨多轮存在；会话结束后自然过期，
  不需要 harness 那种复杂的轮次预算和多轮驱动。
- 轻量实现：session-level JSON 文件，append-only 变更日志 + 当前快照。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

GOAL_DIR_NAME = "goals"
MAX_GOALS_PER_SESSION = 20

class GoalStatus(str):
    active = "active"
    paused = "paused"
    blocked = "blocked"
    completed = "completed"
    cleared = "cleared"

    VALID = {"active", "paused", "blocked", "completed", "cleared"}


@dataclass
class GoalEvent:
    ts: float
    action: str
    session_id: str
    description: str = ""
    status: str = ""
    progress: str = ""
    block_reason: str = ""


@dataclass
class GoalState:
    session_id: str
    created_at: float
    description: str = ""
    status: str = GoalStatus.active
    progress: str = ""
    block_reason: str = ""
    history: list[dict] = field(default_factory=list)


def _goal_path(config: Config, session_id: str) -> Path:
    base = config.path(config.paths.history_dir) / GOAL_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.json"


def _load(config: Config, session_id: str) -> GoalState | None:
    path = _goal_path(config, session_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return GoalState(**data)


def _save(state: GoalState, config: Config) -> None:
    path = _goal_path(config, state.session_id)
    data = asdict(state)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_goal(config: Config, session_id: str, description: str) -> dict[str, Any]:
    """建立或更新当前 session 的 goal。"""
    existing = _load(config, session_id)
    now = datetime.now(timezone.utc).timestamp()

    if existing and existing.status in (GoalStatus.active, GoalStatus.paused,
                                        GoalStatus.blocked):
        existing.description = description
        existing.history.append(asdict(GoalEvent(
            ts=now, action="update_description",
            session_id=session_id, description=description,
        )))
        _save(existing, config)
        return {
            "ok": True, "session_id": session_id,
            "description": description, "status": existing.status,
            "message": f"目标已更新：{description[:60]}",
        }

    event = GoalEvent(ts=now, action="create", session_id=session_id,
                      description=description)
    state = GoalState(
        session_id=session_id, created_at=now, description=description,
        status=GoalStatus.active, history=[asdict(event)],
    )
    _save(state, config)
    return {
        "ok": True, "session_id": session_id,
        "description": description, "status": GoalStatus.active,
        "message": f"目标已建立：{description[:60]}",
    }


def update_goal(config: Config, session_id: str, *,
                status: str | None = None,
                description: str | None = None,
                progress: str | None = None,
                block_reason: str | None = None) -> dict[str, Any]:
    """更新当前 session 的 goal 状态。"""
    state = _load(config, session_id)
    if state is None:
        return {"ok": False, "message": "当前没有活跃目标，请先用 create_goal 建立。"}

    now = datetime.now(timezone.utc).timestamp()
    if status is not None and status not in GoalStatus.VALID:
        return {"ok": False, "message": f"无效状态 '{status}'，可选：{', '.join(sorted(GoalStatus.VALID))}"}

    changes: list[str] = []
    if description is not None:
        state.description = description
        changes.append("description")
    if progress is not None:
        state.progress = progress
        changes.append("progress")
    if block_reason is not None:
        state.block_reason = block_reason
        if block_reason:
            state.status = GoalStatus.blocked
        changes.append("block_reason")
    if status is not None:
        state.status = status
        changes.append("status")

    if not changes:
        return {"ok": True, "message": "无变更。"}

    state.history.append(asdict(GoalEvent(
        ts=now, action="update", session_id=session_id,
        description=description or "", status=status or "",
        progress=progress or "", block_reason=block_reason or "",
    )))
    _save(state, config)

    return {
        "ok": True, "session_id": session_id,
        "changes": ", ".join(changes), "status": state.status,
        "message": f"目标已更新（{', '.join(changes)}），状态：{state.status}",
    }


def get_goal(config: Config, session_id: str) -> dict[str, Any] | None:
    state = _load(config, session_id)
    if state is None:
        return None
    return {
        "session_id": state.session_id,
        "description": state.description,
        "status": state.status,
        "progress": state.progress,
        "block_reason": state.block_reason,
        "created_at": state.created_at,
    }