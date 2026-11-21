"""Agent 自调度（schedule）：agent 可以在对话中创建/取消/查看定时任务。

设计依据：
- DeepSeek-Harness schedule_create/schedule_delete/schedule_list 工具
- Alfred 当前 cronjob 只能由 Hermes 侧管理，agent 内部无法 self-schedule
- 全局 JSONL 持久化；CLI 启动和每次等待输入前投递提醒文本。
- 展示成功后才标记 fired；重试不运行 agent 或工具。
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from functools import wraps
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

SCHEDULE_DIR_NAME = "schedules"


@dataclass
class ScheduleEntry:
    id: str
    created_at: float
    user_id: str  # session_id 作为 user_id
    description: str
    due_at: float  # 到期时间（unix timestamp）
    prompt: str
    status: str = "active"
    last_fired: float | None = None


def _schedule_path(config: Config) -> Path:
    base = config.path(config.paths.history_dir) / SCHEDULE_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def _load_all(config: Config) -> list[ScheduleEntry]:
    path = _schedule_path(config) / "schedules.jsonl"
    if not path.exists():
        return []
    entries: list[ScheduleEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(ScheduleEntry(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
    return entries


def _save_all(config: Config, entries: list[ScheduleEntry]) -> None:
    path = _schedule_path(config) / "schedules.jsonl"
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
    os.replace(temp, path)


def _serialized(operation):
    @wraps(operation)
    def run(config, *args, **kwargs):
        with closing(sqlite3.connect(_schedule_path(config) / "delivery.lock")) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            return operation(config, *args, **kwargs)

    return run


@_serialized
def schedule_create(config: Config, session_id: str,
                    description: str, prompt: str,
                    due_at: float | None = None) -> dict[str, Any]:
    """创建一条定时任务。

    Args:
        due_at: 到期时间（unix timestamp）。如果为 None，默认 24 小时后。
    """
    if due_at is None:
        due_at = datetime.now(timezone.utc).timestamp() + 86_400
    entry = ScheduleEntry(
        id=_id(), created_at=datetime.now(timezone.utc).timestamp(),
        user_id=session_id, description=description,
        due_at=due_at, prompt=prompt, status="active",
    )
    entries = _load_all(config)
    entries.append(entry)
    _save_all(config, entries)

    due_dt = datetime.fromtimestamp(due_at, tz=timezone.utc)
    return {
        "ok": True, "id": entry.id,
        "description": description,
        "due_at": due_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "message": f"已创建定时任务 [{entry.id[:6]}]：{description[:40]}，到期：{due_dt:%m-%d %H:%M} UTC",
    }


@_serialized
def schedule_delete(config: Config, schedule_id: str) -> dict[str, Any]:
    """取消一条定时任务。"""
    entries = _load_all(config)
    before = len(entries)
    entries = [e for e in entries if not (e.id == schedule_id or e.id.startswith(schedule_id))]
    if len(entries) == before:
        return {"ok": False, "message": f"未找到定时任务：{schedule_id}"}
    _save_all(config, entries)
    cancelled = before - len(entries)
    return {
        "ok": True, "cancelled": cancelled,
        "message": f"已取消 {cancelled} 条定时任务（匹配 {schedule_id}）",
    }


def schedule_list(config: Config, session_id: str | None = None) -> dict[str, Any]:
    """列出定时任务。

    不传 session_id 时列出全部（跨 session 全局可见）；
    传入时仅列该 session 的任务（兼容旧 agent 调用方式）。
    """
    entries = _load_all(config)
    if session_id:
        entries = [e for e in entries if e.user_id == session_id]
    rows = []
    for e in sorted(entries, key=lambda x: x.due_at):
        due_dt = datetime.fromtimestamp(e.due_at, tz=timezone.utc)
        rows.append({
            "id": e.id[:8],
            "session_id": e.user_id[:8],
            "description": e.description[:60],
            "due_at": due_dt.strftime("%Y-%m-%d %H:%M UTC"),
            "status": e.status,
        })
    return {"count": len(rows), "entries": rows}


@_serialized
def deliver_due(config: Config, notify) -> int:
    """Deliver text only; never hand a retryable reminder to the agent as a task."""
    now = datetime.now(timezone.utc).timestamp()
    entries = _load_all(config)
    delivered = 0
    for entry in entries:
        if entry.due_at > now or entry.status not in {"active", "delivering", "failed"}:
            continue
        entry.status = "delivering"
        _save_all(config, entries)
        try:
            notify(entry)
        except Exception:
            entry.status = "failed"
            _save_all(config, entries)
            raise
        entry.status = "fired"
        entry.last_fired = now
        _save_all(config, entries)
        delivered += 1
    return delivered
