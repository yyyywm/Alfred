"""Agent 自调度（schedule）：agent 可以在对话中创建/取消/查看定时任务。

设计依据：
- DeepSeek-Harness schedule_create/schedule_delete/schedule_list 工具
- Alfred 当前 cronjob 只能由 Hermes 侧管理，agent 内部无法 self-schedule
- 轻量实现：全局 JSONL 文件持久化，不依赖外部 cron，由
  Alfred 启动时（chat 命令）与每轮对话前加载已到期任务注入 prompt 实现"定时提醒"。
- 跨 session 全局化：一次创建的 schedule 在任意 session 都能看到和触发，
  到期后全局标记 fired（而不是 session 独占），避免换 session 就丢失。

注意：真正的定时执行仍需要 Hermes 侧 cron 触发；本模块负责 agent 侧的
声明与持久化。schedule_fire_pending 是全局入口，供 CLI 调用。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
    prompt: str  # 到期时注入 agent 的 prompt
    status: str = "active"  # active / fired / cancelled
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
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")


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


def schedule_fire_pending(config: Config) -> list[str]:
    """返回所有已到期且未触发的 active 任务的 prompt，并将它们标记为 fired。

    供 Alfred 启动时调用，把到期任务注入 agent 的 prompt。
    """
    now = datetime.now(timezone.utc).timestamp()
    entries = _load_all(config)
    fired_prompts: list[str] = []
    for e in entries:
        if e.status == "active" and e.due_at <= now:
            e.status = "fired"
            e.last_fired = now
            fired_prompts.append(e.prompt)
    _save_all(config, entries)
    return fired_prompts