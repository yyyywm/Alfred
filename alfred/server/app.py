"""FastAPI Web 应用：REST API + WebSocket 聊天 + 静态文件。

内存优化设计：
  - AppContext: 应用启动时一次性加载所有静态数据（配置、技能、规则、索引），
    所有请求共享，避免每个 API 调用/每个 WebSocket 重复 I/O。
  - WebSession 不再单独加载技能/规则/索引，从 AppContext 获取。
  - config 只加载一次（除非显式调用 reload_config）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, load_config
from ..history import (
    Session,
    delete_session,
    get_title,
    list_sessions,
    session_preview,
    set_title,
)
from ..memory.blocks import MemoryBlocks
from ..memory.lessons import LessonsBlock
from .session import WebSession

logger = logging.getLogger(__name__)

_WEBUI_DIR = Path(__file__).parent.parent / "webui"


# ── 应用上下文：一次性加载共享数据 ──────────────────────────────────


class AppContext:
    """应用级共享上下文，所有请求/会话共用。

    在 create_app() 中初始化一次，之后所有 API 和 WebSocket 通过 ctx 访问，
    避免重复 I/O（技能扫描、规则扫描、代码库索引、笔记目录等）。
    """

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self.config: Config = load_config(Path(config_path) if config_path else None)
        self.blocks: MemoryBlocks = MemoryBlocks(self.config)
        self.lessons: LessonsBlock = LessonsBlock(self.config)

        # 一次性加载静态数据
        self._skill_index: str = ""
        self._lessons_text: str = ""
        self._rules_text: str = ""
        self._notes_catalog: str = ""
        self._codebase_index: str = ""
        self._load_static_data()

        # 线程安全锁（模型切换时更新 config）
        self._lock = threading.Lock()

    def _load_static_data(self) -> None:
        """一次性加载所有静态数据。"""
        from ..skills.loader import scan_skills, render_skills_index
        from ..rules.loader import scan_rules, render_rules
        from ..knowledge.catalog import load_catalog
        from ..codebase_index import ensure_index

        self._skill_index = render_skills_index(scan_skills(self.config))

        # 教训文本
        try:
            text = self.lessons.read().strip()
        except Exception:
            text = ""
        if text and "还没有教训" not in text:
            self._lessons_text = f"# 你从过去中学到的教训（RefleXion 教训库）\n{text}"

        # 规则
        always_rules, recall_rules = render_rules(scan_rules(self.config))
        self._rules_text = "\n\n".join(t for t in (always_rules, recall_rules) if t)

        # 笔记目录
        try:
            self._notes_catalog = load_catalog(self.config)
        except Exception:
            self._notes_catalog = ""

        # 代码库索引
        try:
            self._codebase_index = ensure_index(self.config)
        except Exception:
            self._codebase_index = ""

    def reload_config(self) -> None:
        """重新加载配置（模型切换后需要）。"""
        with self._lock:
            self.config = load_config(Path(self.config_path) if self.config_path else None)

    def get_config(self) -> Config:
        with self._lock:
            return self.config

    def update_model(self, model_ref: str) -> None:
        """切换闲聊模型。"""
        with self._lock:
            self.config.resolve(model_ref)
            self.config.models.chat = model_ref


# ── 应用工厂 ──────────────────────────────────────────────────────


def create_app(config_path: str | None = None) -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(title="Alfred Web UI", version="0.1.0")

    # 挂载静态文件
    if _WEBUI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_WEBUI_DIR / "static")), name="static")

    # 初始化共享上下文
    ctx = AppContext(config_path)

    # ── 首页 ──

    @app.get("/")
    async def index():
        index_file = _WEBUI_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse({"message": "Alfred Web UI"})

    # ── 会话管理 REST API ──

    @app.get("/api/sessions")
    async def api_list_sessions():
        config = ctx.get_config()
        sessions = list_sessions(config)
        result = []
        for s in sessions:
            preview = session_preview(config, s.id)
            result.append({
                "id": s.id,
                "title": s.title,
                "title_auto": s.title_auto,
                "mtime": s.mtime,
                "msg_count": s.msg_count,
                "preview": preview,
            })
        return result

    @app.post("/api/sessions")
    async def api_create_session():
        config = ctx.get_config()
        s = Session(config)
        s.file.touch()
        return {"id": s.id}

    @app.delete("/api/sessions/{session_id}")
    async def api_delete_session(session_id: str):
        config = ctx.get_config()
        deleted = delete_session(config, session_id)
        if not deleted:
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        return {"deleted": True}

    @app.patch("/api/sessions/{session_id}/title")
    async def api_set_title(session_id: str, body: dict[str, Any]):
        config = ctx.get_config()
        title = body.get("title", "")
        if not title:
            return JSONResponse(status_code=400, content={"error": "标题不能为空"})
        set_title(config, session_id, title, auto=False)
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/messages")
    async def api_get_messages(session_id: str):
        from dataclasses import asdict
        config = ctx.get_config()
        s = Session(config, session_id=session_id)
        if not s.file.exists():
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        messages = []
        for m in s.messages:
            msg = asdict(m)
            if len(msg["content"]) > 5000:
                msg["content"] = msg["content"][:5000] + "…"
            for tc in msg.get("tool_calls", []):
                if len(tc.get("result", "")) > 2000:
                    tc["result"] = tc["result"][:2000] + "…"
            messages.append(msg)
        return messages

    # ── 记忆块 REST API ──
    # 注意：/api/memory/lessons 必须在 /api/memory/{block} 之前定义

    @app.get("/api/memory/lessons")
    async def api_get_lessons():
        content = ctx.lessons.read().strip()
        limit = ctx.config.memory.lessons_block_char_limit
        return {
            "block": "lessons",
            "content": content,
            "char_count": len(content),
            "char_limit": limit,
        }

    @app.get("/api/memory/{block}")
    async def api_get_memory_block(block: str):
        if block not in ("human", "persona"):
            return JSONResponse(status_code=400, content={"error": "不支持的记忆块"})
        content = ctx.blocks.read(block)
        limit = ctx.config.memory.limit_for(block)
        return {
            "block": block,
            "content": content,
            "char_count": len(content),
            "char_limit": limit,
        }

    @app.put("/api/memory/lessons")
    async def api_update_lessons(body: dict[str, Any]):
        content = body.get("content", "")
        ctx.lessons.update(content)
        return {"ok": True}

    @app.put("/api/memory/{block}")
    async def api_update_memory_block(block: str, body: dict[str, Any]):
        if block not in ("human", "persona"):
            return JSONResponse(status_code=400, content={"error": "不支持的记忆块"})
        content = body.get("content", "")
        reason = body.get("reason", "Web UI 手动编辑")
        result = ctx.blocks.update(block, content, reason)
        return {"ok": True, "result": result}

    # ── 状态与配置 REST API ──

    @app.get("/api/status")
    async def api_status():
        config = ctx.get_config()
        status = {
            "chat_model": config.models.chat,
            "memory_write_model": config.models.memory_write,
            "embed_model": config.models.embed.name,
            "embed_provider": config.models.embed.provider,
        }
        from ..memory import longterm
        init_status = longterm.init_status()
        status["memory_layer"] = "ok" if not init_status else f"error: {init_status}"
        status["memory_provider"] = config.memory.provider
        return status

    @app.get("/api/models")
    async def api_list_models():
        config = ctx.get_config()
        models = []
        for name, provider in config.providers.items():
            for model in provider.models:
                models.append({
                    "ref": f"{name}:{model}",
                    "name": model,
                    "provider": name,
                    "type": provider.type,
                })
        return models

    @app.patch("/api/model")
    async def api_set_model(body: dict[str, Any]):
        model_ref = body.get("model", "")
        if not model_ref:
            return JSONResponse(status_code=400, content={"error": "model 不能为空"})
        try:
            ctx.update_model(model_ref)
        except (KeyError, ValueError) as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        return {"ok": True, "model": model_ref}

    @app.get("/api/skills")
    async def api_list_skills():
        from ..skills.loader import scan_skills
        skills = scan_skills(ctx.get_config())
        return [
            {
                "name": s.name,
                "description": s.description,
                "when_to_use": getattr(s, "when_to_use", "") or "",
                "disable_model_invocation": getattr(s, "disable_model_invocation", False),
                "source": "bundled" if s.path and "bundled" in str(s.path) else "user",
            }
            for s in skills
        ]

    @app.get("/api/whoami")
    async def api_whoami():
        config = ctx.get_config()
        info = {"chat_model": config.models.chat, "memory_blocks": {}, "lessons_count": 0}
        for block in ("human", "persona"):
            content = ctx.blocks.read(block)
            info["memory_blocks"][block] = {
                "char_count": len(content),
                "char_limit": config.memory.limit_for(block),
            }
        try:
            content = ctx.lessons.read().strip()
            info["lessons_count"] = content.count("\n") + 1 if content else 0
        except Exception:
            pass
        return info

    # ── WebSocket 聊天 ──

    @app.websocket("/ws/chat/{session_id}")
    async def websocket_chat(ws: WebSocket, session_id: str):
        """WebSocket 聊天端点。"""
        await ws.accept()

        config = ctx.get_config()
        ws_session = WebSession(
            config,
            session_id=session_id,
            # 从共享上下文获取静态数据，避免重复加载
            skill_index=ctx._skill_index,
            lessons_text=ctx._lessons_text,
            rules_text=ctx._rules_text,
            notes_catalog=ctx._notes_catalog,
            codebase_index=ctx._codebase_index,
            blocks=ctx.blocks,
            lessons=ctx.lessons,
        )
        loop = asyncio.get_event_loop()
        ws_session.bind_loop(loop)

        await ws.send_json({
            "type": "session_info",
            "session_id": ws_session.get_session_id(),
            "model": config.models.chat,
        })

        async def sender():
            try:
                while True:
                    event = await ws_session.event_queue.get()
                    await ws.send_json(event)
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("sender 异常")

        turn_task: asyncio.Task | None = None

        async def receiver():
            nonlocal turn_task
            try:
                while True:
                    msg = await ws.receive_json()
                    msg_type = msg.get("type", "")
                    if msg_type == "message":
                        text = msg.get("text", "").strip()
                        if not text:
                            continue
                        if turn_task and not turn_task.done():
                            await ws.send_json({
                                "type": "error",
                                "error": "当前对话仍在进行中，请等待完成",
                            })
                            continue
                        turn_task = asyncio.create_task(ws_session.start_turn(text))
                    elif msg_type == "confirm":
                        confirm_id = msg.get("id", "")
                        approved = msg.get("approved", False)
                        ok = ws_session.respond_confirm(confirm_id, approved)
                        if not ok:
                            await ws.send_json({
                                "type": "error",
                                "error": f"确认请求 {confirm_id} 已过期或不存在",
                            })
                    elif msg_type == "model":
                        model_ref = msg.get("model", "")
                        try:
                            ctx.update_model(model_ref)
                            ws_session.switch_model(model_ref)
                            await ws.send_json({"type": "model_changed", "model": model_ref})
                        except (KeyError, ValueError) as e:
                            await ws.send_json({"type": "error", "error": str(e)})
                    elif msg_type == "ping":
                        await ws.send_json({"type": "pong"})
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("receiver 异常")

        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())

        try:
            done, pending = await asyncio.wait(
                [sender_task, receiver_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception:
            logger.exception("WebSocket 会话异常")
            sender_task.cancel()
            receiver_task.cancel()

    return app


# 模块级 app 实例
_app: FastAPI | None = None


def get_app(config_path: str | None = None) -> FastAPI:
    global _app
    if _app is None:
        _app = create_app(config_path)
    return _app
