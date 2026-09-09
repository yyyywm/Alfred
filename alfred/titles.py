"""会话标题的自动概括：首轮对话后后台生成，手动标题优先。

设计要点：
- 生成走 models.chat 当前模型，后台 daemon 线程执行，失败静默降级记日志，
  不阻塞对话、不打印到终端（避免污染 prompt_toolkit 输入）。
- write_auto_title 走原子 check-and-set：已有标题（含自动标题）永不被覆盖
  ——手动标题优先由此保证。
"""

from __future__ import annotations

import logging
import threading

from .config import Config
from .history import set_title_if_absent

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 30

_PROMPT = (
    "用不超过15个字概括这段对话的主题，作为会话标题。"
    "只输出标题本身：不要引号、不要句号结尾、不要解释。\n\n"
    "用户：{user}\n\n助手：{assistant}"
)


def _clean_title(raw: str) -> str | None:
    """清洗 LLM 输出：取首行、去引号、超长截断；空则 None。"""
    text = raw.strip()
    if not text:
        return None
    title = text.splitlines()[0].strip().strip("\"'「」。《》“”‘’【】")
    if not title:
        return None
    return title[:MAX_TITLE_CHARS]


def generate_title(config: Config, user_text: str, assistant_text: str) -> str | None:
    """同步调用 chat 模型概括标题；任何失败返回 None。"""
    from pydantic_ai import Agent
    from pydantic_ai.settings import ModelSettings

    from .llm import build_model

    try:
        model = build_model(config, config.models.chat)
        agent = Agent(model, system_prompt="你是会话标题概括器。")
        result = agent.run_sync(
            _PROMPT.format(user=user_text[:500], assistant=assistant_text[:500]),
            model_settings=ModelSettings(timeout=20, max_tokens=50),
        )
        # pydantic-ai 新版为 result.output，旧版为 result.data
        raw = getattr(result, "output", None) or getattr(result, "data", None)
        return _clean_title(raw)
    except Exception as e:
        logger.warning("自动生成会话标题失败: %s", e)
        return None


def write_auto_title(config: Config, session_id: str, title: str) -> bool:
    """仅当会话尚无标题时写入自动标题（原子 check-and-set）；返回是否写入。"""
    return set_title_if_absent(config, session_id, title)


def maybe_generate_title_async(
    config: Config, session_id: str, user_text: str, assistant_text: str
) -> None:
    """后台线程生成并写入标题；任何失败静默降级。"""

    def _run() -> None:
        title = generate_title(config, user_text, assistant_text)
        if title:
            write_auto_title(config, session_id, title)

    threading.Thread(target=_run, daemon=True).start()
