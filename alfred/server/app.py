"""FastAPI Web 应用：REST API + WebSocket 聊天 + 静态文件。

路由：
  REST API:
    GET    /api/sessions           — 列出所有会话
    POST   /api/sessions           — 创建新会话
    DELETE /api/sessions/{id}      — 删除会话
    PATCH  /api/sessions/{id}/title — 设置会话标题
    GET    /api/sessions/{id}/messages — 获取会话历史
    GET    /api/memory/{block}     — 读取记忆块（human/persona/lessons）
    PUT    /api/memory/{block}     — 更新记忆块
    GET    /api/status             — 模型/记忆层连接状态
    GET    /api/models             — 可用模型列表
    PATCH  /api/model              — 切换闲聊模型
    GET    /api/skills             — 已安装技能列表
    GET    /api/whoami             — Alfred 积累状态

  WebSocket:
    /ws/chat/{session_id}          — 聊天事件流

  静态文件:
    /                            — Web UI 首页
    /static/*                     — CSS/JS/库文件
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import load_config
from ..history import (
    Session,
    SessionInfo,
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

# Web UI 静态文件目录
_WEBUI_DIR = Path(__file__).parent.parent / "webui"


def create_app(config_path: str | None = None) -> FastAPI:
    """创建 FastAPI 应用实例。

    Args:
        config_path: 可选的配置文件路径，默认使用 config.yaml

    Returns:
        FastAPI 应用实例
    """
    app = FastAPI(title="Alfred Web UI", version="0.1.0")

    # 挂载静态文件
    if _WEBUI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_WEBUI_DIR / "static")), name="static")

    # ── 首页 ─────────────────────────────────────────────────────────

    @app.get("/")
    async def index():
        """返回首页。"""
        index_file = _WEBUI_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse({"message": "Alfred Web UI"})

    # ── 会话管理 REST API ────────────────────────────────────────────

    @app.get("/api/sessions")
    async def api_list_sessions():
        """列出所有会话。"""
        config = load_config(Path(config_path) if config_path else None)
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
        """创建新会话（写入空文件以便 list_sessions 可见）。"""
        config = load_config(Path(config_path) if config_path else None)
        s = Session(config)
        # 触达文件确保磁盘可见（list_sessions 靠 glob 扫描 .jsonl）
        s.file.touch()
        return {"id": s.id}

    @app.delete("/api/sessions/{session_id}")
    async def api_delete_session(session_id: str):
        """删除会话。"""
        config = load_config(Path(config_path) if config_path else None)
        deleted = delete_session(config, session_id)
        if not deleted:
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        return {"deleted": True}

    @app.patch("/api/sessions/{session_id}/title")
    async def api_set_title(session_id: str, body: dict[str, Any]):
        """设置会话标题。"""
        config = load_config(Path(config_path) if config_path else None)
        title = body.get("title", "")
        if not title:
            return JSONResponse(status_code=400, content={"error": "标题不能为空"})
        set_title(config, session_id, title, auto=False)
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/messages")
    async def api_get_messages(session_id: str):
        """获取会话历史消息。"""
        config = load_config(Path(config_path) if config_path else None)
        s = Session(config, session_id=session_id)
        if not s.file.exists():
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        messages = []
        for m in s.messages:
            from dataclasses import asdict
            msg = asdict(m)
            # 截断超长内容
            if len(msg["content"]) > 5000:
                msg["content"] = msg["content"][:5000] + "…"
            # 截断工具调用结果
            for tc in msg.get("tool_calls", []):
                if len(tc.get("result", "")) > 2000:
                    tc["result"] = tc["result"][:2000] + "…"
            messages.append(msg)
        return messages

    # ── 记忆块 REST API ──────────────────────────────────────────────
    # 注意：/api/memory/lessons 必须在 /api/memory/{block} 之前定义，
    # 否则 "lessons" 会被 {block} 捕获并返回 400。

    @app.get("/api/memory/lessons")
    async def api_get_lessons():
        """读取教训块。"""
        config = load_config(Path(config_path) if config_path else None)
        lb = LessonsBlock(config)
        content = lb.read().strip()
        limit = config.memory.lessons_block_char_limit
        return {
            "block": "lessons",
            "content": content,
            "char_count": len(content),
            "char_limit": limit,
        }

    @app.get("/api/memory/{block}")
    async def api_get_memory_block(block: str):
        """读取记忆块（human/persona）。"""
        config = load_config(Path(config_path) if config_path else None)
        if block not in ("human", "persona"):
            return JSONResponse(status_code=400, content={"error": "不支持的记忆块"})
        b = MemoryBlocks(config)
        content = b.read(block)
        limit = config.memory.limit_for(block)
        return {
            "block": block,
            "content": content,
            "char_count": len(content),
            "char_limit": limit,
        }

    @app.put("/api/memory/lessons")
    async def api_update_lessons(body: dict[str, Any]):
        """更新教训块。"""
        config = load_config(Path(config_path) if config_path else None)
        content = body.get("content", "")
        lb = LessonsBlock(config)
        lb.update(content)
        return {"ok": True}

    @app.put("/api/memory/{block}")
    async def api_update_memory_block(block: str, body: dict[str, Any]):
        """更新记忆块（需要用户确认，Web 端确认后调用）。"""
        config = load_config(Path(config_path) if config_path else None)
        if block not in ("human", "persona"):
            return JSONResponse(status_code=400, content={"error": "不支持的记忆块"})
        content = body.get("content", "")
        reason = body.get("reason", "Web UI 手动编辑")
        b = MemoryBlocks(config)
        result = b.update(block, content, reason)
        return {"ok": True, "result": result}

    # ── 状态与配置 REST API ─────────────────────────────────────────

    @app.get("/api/status")
    async def api_status():
        """获取模型与记忆层连接状态。"""
        config = load_config(Path(config_path) if config_path else None)
        status = {
            "chat_model": config.models.chat,
            "memory_write_model": config.models.memory_write,
            "embed_model": config.models.embed.name,
            "embed_provider": config.models.embed.provider,
        }
        # 检查记忆层状态
        from ..memory import longterm
        init_status = longterm.init_status()
        status["memory_layer"] = "ok" if not init_status else f"error: {init_status}"
        status["memory_provider"] = config.memory.provider
        return status

    @app.get("/api/models")
    async def api_list_models():
        """列出所有可用模型。"""
        config = load_config(Path(config_path) if config_path else None)
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
        """切换闲聊模型。"""
        config = load_config(Path(config_path) if config_path else None)
        model_ref = body.get("model", "")
        if not model_ref:
            return JSONResponse(status_code=400, content={"error": "model 不能为空"})
        try:
            config.resolve(model_ref)
        except (KeyError, ValueError) as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        config.models.chat = model_ref
        return {"ok": True, "model": model_ref}

    @app.get("/api/skills")
    async def api_list_skills():
        """列出已安装的技能。"""
        from ..skills.loader import scan_skills
        config = load_config(Path(config_path) if config_path else None)
        skills = scan_skills(config)
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
        """获取 Alfred 的积累状态。"""
        config = load_config(Path(config_path) if config_path else None)
        info = {
            "chat_model": config.models.chat,
            "memory_blocks": {},
            "lessons_count": 0,
        }
        b = MemoryBlocks(config)
        for block in ("human", "persona"):
            content = b.read(block)
            info["memory_blocks"][block] = {
                "char_count": len(content),
                "char_limit": config.memory.limit_for(block),
            }
        try:
            lb = LessonsBlock(config)
            content = lb.read().strip()
            info["lessons_count"] = content.count("\n") + 1 if content else 0
            info["lessons_blocks"]["lessons"] = {
                "char_count": len(content),
                "char_limit": config.memory.lessons_block_char_limit,
            }
        except Exception:
            pass
        return info

    # ── WebSocket 聊天 ──────────────────────────────────────────────

    @app.websocket("/ws/chat/{session_id}")
    async def websocket_chat(ws: WebSocket, session_id: str):
        """WebSocket 聊天端点：事件流 + 消息接收。

        客户端发送格式:
          {"type": "message", "text": "..."}       — 发送用户消息
          {"type": "confirm", "id": "...", "approved": bool}  — 响应确认
          {"type": "model", "model": "provider:model"} — 切换模型
          {"type": "ping"}                         — 心跳

        服务端发送格式:
          {"type": "turn_start", "session_id": "..."}
          {"type": "assistant_chunk", "delta": "..."}
          {"type": "tool_call_start", "tool_name": "...", "args": {...}}
          {"type": "tool_call_end", "tool_name": "...", "is_error": bool, "result": "..."}
          {"type": "tool_denied", "tool_name": "...", "reason": "..."}
          {"type": "turn_end", "usage": {...}}
          {"type": "turn_error", "error": "..."}
          {"type": "turn_retrying", "attempt": n, "wait_seconds": n}
          {"type": "context_compacted", "summary": "..."}
          {"type": "confirm_request", "id": "...", "message": "...", "tool_name": "..."}
          {"type": "turn_complete"}
          {"type": "error", "error": "..."}
        """
        await ws.accept()

        config = load_config(Path(config_path) if config_path else None)
        ws_session = WebSession(config, session_id=session_id)
        loop = asyncio.get_event_loop()
        ws_session.bind_loop(loop)

        # 发送会话信息
        await ws.send_json({
            "type": "session_info",
            "session_id": ws_session.get_session_id(),
            "model": config.models.chat,
        })

        # sender 协程：从 event_queue 读取事件，发送到 WebSocket
        async def sender():
            """持续从事件队列读取并发送到 WebSocket。"""
            try:
                while True:
                    event = await ws_session.event_queue.get()
                    await ws.send_json(event)
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                logger.exception("sender 异常")

        # receiver 协程：从 WebSocket 接收消息
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
                        # 如果有正在进行的轮次，拒绝新消息
                        if turn_task and not turn_task.done():
                            await ws.send_json({
                                "type": "error",
                                "error": "当前对话仍在进行中，请等待完成",
                            })
                            continue
                        turn_task = asyncio.create_task(
                            ws_session.start_turn(text)
                        )
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
                            ws_session.switch_model(model_ref)
                            await ws.send_json({"type": "model_changed", "model": model_ref})
                        except (KeyError, ValueError) as e:
                            await ws.send_json({"type": "error", "error": str(e)})
                    elif msg_type == "ping":
                        await ws.send_json({"type": "pong"})
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                logger.exception("receiver 异常")

        # 并发运行 sender 和 receiver
        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())

        try:
            # 等待任一协程结束（WebSocket 断开时 receiver 会结束）
            done, pending = await asyncio.wait(
                [sender_task, receiver_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # 清理未完成的协程
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception as exc:
            logger.exception("WebSocket 会话异常")
            sender_task.cancel()
            receiver_task.cancel()

    return app


# 模块级 app 实例（供 `alfred web` CLI 命令使用）
_app: FastAPI | None = None


def get_app(config_path: str | None = None) -> FastAPI:
    """获取或创建全局 FastAPI 应用实例。"""
    global _app
    if _app is None:
        _app = create_app(config_path)
    return _app
