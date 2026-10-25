"""Web UI 后端：FastAPI + WebSocket 事件流。

设计要点：
- 复用现有 agent.py 的 chat_turn_stream + EventBus，不重复造轮子
- confirm 回调通过 threading.Event 桥接到 WebSocket，实现浏览器端确认
- 事件通过 asyncio.Queue 从同步线程转发到异步 WebSocket 处理
- REST API 复用 history.py / config.py / memory.blocks 等纯逻辑模块
"""

from .app import create_app

__all__ = ["create_app"]
