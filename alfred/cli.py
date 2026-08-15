"""CLI 入口：alfred chat / ingest / feed / consolidate / memory / models / skills。

chat 内斜杠命令：
  /exit 退出  /new 新会话  /model <provider:model> 切换闲聊模型
  /remember <内容> 显式教学（写入 human 块）
  /memory 查看长期记忆  /why 查看上一轮用了哪些记忆
  /sessions 列出历史会话  /load <序号或id> 加载历史会话  /delete <序号或id> 删除会话
  /lessons 查看管家从过去中学到的教训（RefleXion 教训库）
  /whoami 查看 Alfred 的积累状态（记忆/教训/情景/笔记/框架）
  /status 检查当前模型与 embedding 连接
"""

from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from datetime import datetime

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# Windows 上 asyncio 默认的 ProactorEventLoop 在 Ctrl+C 退出时会报
# "Cancelling an overlapped future failed / WinError 6"（prompt_toolkit 的已知问题）。
# 换成 SelectorEventLoop 可避免该报错；本项目不使用 asyncio 子进程，切换无影响。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alfred.events import (
    AssistantChunk,
    ContextCompacted,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    ToolDenied,
    TurnEnd,
    TurnError,
    TurnStart,
    SkillSuggested,
)

from .agent import AlfredDeps, build_agent, chat_turn_stream
from .config import load_config
from .history import Session, delete_session, list_sessions
from .memory import longterm
from .memory.blocks import MemoryBlocks
from .memory.lessons import LessonsBlock
from .skills.loader import render_skills_index, scan_skills

app = typer.Typer(help="私人管家 AI Agent", no_args_is_help=True)
console = Console()

_LOGGER_NAME = "alfred.chat"


def _load_lessons_text(config: Config) -> str:
    """一次性读好 lessons 文本，供 inject_lessons 使用。"""
    try:
        lb = LessonsBlock(config)
        text = lb.read().strip()
    except Exception:
        return ""
    if not text or "还没有教训" in text:
        return ""
    return f"# 你从过去中学到的教训（RefleXion 教训库）\n{text}"


def _confirm(msg: str) -> bool:
    """在工作线程中请求用户确认；避开 Rich Console 以减少线程竞争。"""
    print(f"\n{'=' * 60}\n确认请求\n{'=' * 60}\n{msg}\n{'=' * 60}")
    answer = input("是否允许 [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _resolve_session_ref(config, ref: str, listed: list[tuple[str, float, int]]) -> str | None:
    """把 /load、/delete 的参数解析为会话 id：支持列表序号或 id 前缀。"""
    if ref.isdigit():
        idx = int(ref) - 1
        return listed[idx][0] if 0 <= idx < len(listed) else None
    for sid, _mtime, _count in list_sessions(config):
        if sid.startswith(ref):
            return sid
    return None


def _setup_chat_logger(config, debug: bool = False) -> logging.Logger:
    """配置 chat 日志：默认写入 data/logs/alfred.log，debug 时同时输出到控制台。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    log_dir = config.path(config.paths.history_dir).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "alfred.log"

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    if debug:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(
            "[dim]%(asctime)s [%(levelname)s] %(message)s[/dim]"
        ))
        stream_handler.setLevel(logging.DEBUG)
        logger.addHandler(stream_handler)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _print_connection_result(ref: str, result: dict) -> None:
    if result["ok"]:
        console.print(f"  [green]✓[/green]  {ref}  {result['latency_ms']:.0f}ms")
    else:
        console.print(f"  [red]✗[/red]  {ref}  {result['error']}")


@app.command()
def models(
    model_ref: str | None = typer.Argument(None, help="要测试的 provider:model"),
    all_models: bool = typer.Option(False, "--all", help="测试所有配置的模型"),
):
    """列出配置的 provider 与模型，检查 key 可用性；可测试单个或全部模型与 embedding 连通性。"""
    from .llm import check_embed_connection, check_model_connection, list_models

    config = load_config()

    rows = list_models(config)
    if not rows:
        console.print("[red]config.yaml 中没有配置任何 provider。[/red]")
        raise typer.Exit(1)

    # 默认：列出配置，不调用 API
    if not all_models and not model_ref:
        for ref, ptype, ready in rows:
            mark = "[green]✓[/green]" if ready else "[red]✗ key 未设置[/red]"
            console.print(f"  {mark}  {ref}  ({ptype})")
        console.print(f"\n闲聊模型：{config.models.chat}")
        console.print(f"记忆写入模型：{config.models.memory_write}")
        embed = config.models.embed
        console.print(f"Embedding：{embed.provider} / {embed.name}")
        return

    if all_models and model_ref:
        console.print("[red]不能同时指定 model_ref 和 --all。[/red]")
        raise typer.Exit(1)

    if all_models:
        any_failed = False
        for ref, _ptype, _ready in rows:
            with console.status(f"[dim]测试 {ref} ...[/dim]"):
                result = check_model_connection(config, ref)
            if not result["ok"]:
                any_failed = True
            _print_connection_result(ref, result)
        embed_label = f"embedding:{config.models.embed.provider}"
        with console.status(f"[dim]测试 {embed_label} ...[/dim]"):
            result = check_embed_connection(config)
        if not result["ok"]:
            any_failed = True
        _print_connection_result(embed_label, result)
        if any_failed:
            raise typer.Exit(1)
        return

    if model_ref:
        try:
            config.resolve(model_ref)
        except (KeyError, ValueError) as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        with console.status(f"[dim]测试 {model_ref} ...[/dim]"):
            result = check_model_connection(config, model_ref)
        _print_connection_result(model_ref, result)
        if not result["ok"]:
            raise typer.Exit(1)
        return


@app.command()
def chat(
    session_id: str = typer.Option(None, "--session", "-s", help="恢复指定会话"),
    debug: bool = typer.Option(False, "--debug", help="启用调试日志输出到控制台"),
):
    """开始与管家对话。"""
    config = load_config()
    blocks = MemoryBlocks(config)
    session = Session(config, session_id=session_id)
    agent = build_agent(config)
    # 会话内不变的数据，build_agent 时一次读好，避免每轮系统 prompt 渲染都触发 I/O
    _skill_index = render_skills_index(scan_skills(config))
    _lessons_text = _load_lessons_text(config)
    deps = AlfredDeps(
        config=config,
        blocks=blocks,
        confirm=_confirm,
        skill_index=_skill_index,
        lessons_text=_lessons_text,
    )
    logger = _setup_chat_logger(config, debug=debug)

    console.print(Panel(
        f"会话 {session.id} ｜ 模型 {config.models.chat} ｜ 输入 /exit 退出，/help 查看命令",
        title="[bold]私人管家[/bold]",
    ))
    logger.info("会话开始: %s, 模型: %s, debug: %s", session.id, config.models.chat, debug)

    prompt_session = PromptSession(
        "你: ",
        style=Style.from_dict({"prompt": "cyan bold"}),
    )

    turn_count = 0
    # 每隔 N 轮对话，在回复后提示用户考虑复盘（让巩固动作浮出水面）
    _CONSOLIDATE_REMINDER_EVERY = 10
    listed_sessions: list[tuple[str, float, int]] = []
    while True:
        try:
            user_input = prompt_session.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd, _, arg = user_input.partition(" ")
            arg = arg.strip()
            if cmd in ("/exit", "/quit"):
                break
            elif cmd == "/help":
                console.print(__doc__)
            elif cmd == "/new":
                session = Session(config)
                console.print(f"[dim]新会话 {session.id}[/dim]")
                logger.info("新会话: %s", session.id)
            elif cmd == "/model":
                if not arg:
                    console.print(f"当前模型：{config.models.chat}（切换：/model provider:model）")
                else:
                    try:
                        config.resolve(arg)
                        config.models.chat = arg
                        agent = build_agent(config, arg)
                        console.print(f"[green]已切换模型：{arg}[/green]")
                        logger.info("切换模型: %s", arg)
                    except (KeyError, ValueError) as e:
                        console.print(f"[red]{e}[/red]")
            elif cmd == "/remember":
                if not arg:
                    console.print("用法：/remember <要记住的内容>")
                else:
                    _remember(config, blocks, arg)
            elif cmd == "/memory":
                _show_memory(config)
            elif cmd == "/why":
                if deps.last_recalled:
                    console.print(Panel("\n".join(f"- {m}" for m in deps.last_recalled),
                                        title="上一轮回答依据的记忆"))
                else:
                    console.print("[dim]上一轮没有使用长期记忆。[/dim]")
            elif cmd == "/status":
                _show_status(config)
            elif cmd == "/whoami":
                _show_whoami(config, blocks)
            elif cmd == "/lessons":
                _show_lessons(config, arg)
            elif cmd == "/sessions":
                listed_sessions = list_sessions(config)[:10]
                if not listed_sessions:
                    console.print("[dim]还没有历史会话。[/dim]")
                for i, (sid, mtime, n) in enumerate(listed_sessions, 1):
                    current = "（当前）" if sid == session.id else ""
                    console.print(
                        f"  {i}. {sid}  {datetime.fromtimestamp(mtime):%m-%d %H:%M}  {n} 条消息{current}"
                    )
            elif cmd == "/load":
                if not arg:
                    console.print("用法：/load <序号或会话id>（序号见 /sessions）")
                else:
                    sid = _resolve_session_ref(config, arg, listed_sessions)
                    if sid is None:
                        console.print(f"[red]找不到会话：{arg}[/red]")
                    elif sid == session.id:
                        console.print("[dim]当前已经在该会话。[/dim]")
                    else:
                        session = Session(config, session_id=sid)
                        console.print(
                            f"[green]已加载会话 {sid}（{len(session.messages)} 条消息），继续之前的上下文。[/green]"
                        )
                        logger.info("加载会话: %s", sid)
            elif cmd == "/delete":
                if not arg:
                    console.print("用法：/delete <序号或会话id>（序号见 /sessions）")
                else:
                    sid = _resolve_session_ref(config, arg, listed_sessions)
                    if sid is None:
                        console.print(f"[red]找不到会话：{arg}[/red]")
                    elif sid == session.id:
                        console.print("[red]不能删除当前会话。[/red]")
                    elif _confirm(f"删除会话 {sid}？该操作不可恢复。"):
                        if delete_session(config, sid):
                            console.print(f"[green]已删除会话 {sid}[/green]")
                            logger.info("删除会话: %s", sid)
                        else:
                            console.print(f"[red]会话不存在：{sid}[/red]")
            else:
                console.print(f"[red]未知命令 {cmd}，输入 /help 查看。[/red]")
            continue

        # 分隔用户输入与助手输出
        console.print()
        turn_count += 1
        logger.info("第 %d 轮输入，长度: %d", turn_count, len(user_input))

        reply_parts: list[str] = []
        tool_lines: list[str] = []
        region_saved = False
        status = console.status("[bold green]助手正在思考...[/bold green]", spinner="dots")
        status.start()
        status_active = True

        def stop_status() -> None:
            nonlocal status_active
            if status_active:
                status.stop()
                status_active = False

        def save_cursor() -> None:
            console.file.write("\x1b[s")
            console.file.flush()

        def restore_and_clear() -> None:
            console.file.write("\x1b[u\x1b[J")
            console.file.flush()

        first_content_received = False
        is_tty = console.is_terminal

        try:
            for event in chat_turn_stream(agent, deps, session, user_input, bus=EventBus()):
                logger.debug("事件: %s", type(event).__name__)
                if isinstance(event, AssistantChunk):
                    if not first_content_received:
                        stop_status()
                        first_content_received = True
                    if is_tty and not region_saved:
                        save_cursor()
                        region_saved = True
                        console.print("[bold green]助手：[/bold green] ", end="")
                    if is_tty:
                        console.out(event.delta, end="")
                    reply_parts.append(event.delta)
                elif isinstance(event, ToolCallStart):
                    if not first_content_received:
                        stop_status()
                        first_content_received = True
                    line = f"[dim]🔧 {event.tool_name} ...[/dim]"
                    console.print(line)
                    tool_lines.append(line)
                    logger.info("工具调用: %s, args: %s", event.tool_name, event.args)
                elif isinstance(event, ToolCallEnd):
                    mark = "[green]✓[/green]" if not event.is_error else "[red]✗[/red]"
                    line = f"[dim]🔧 {event.tool_name} {mark}[/dim]"
                    console.print(line)
                    tool_lines.append(line)
                    logger.info("工具结束: %s, 错误: %s", event.tool_name, event.is_error)
                elif isinstance(event, ToolDenied):
                    line = f"[dim]🔧 {event.tool_name} [red]已拒绝[/red][/dim]"
                    console.print(line)
                    tool_lines.append(line)
                    logger.info("工具拒绝: %s", event.tool_name)
                elif isinstance(event, TurnEnd):
                    stop_status()
                    reply = "".join(reply_parts).strip()
                    if region_saved:
                        # 流式原始文本回显 → 回滚该区域 → 重新按 Markdown 渲染
                        restore_and_clear()
                        for line in tool_lines:
                            console.print(line)
                        if reply:
                            console.print("[bold green]助手：[/bold green]")
                            console.print(Markdown(reply))
                    elif reply:
                        console.print("[bold green]助手：[/bold green]")
                        console.print(Markdown(reply))
                elif isinstance(event, SkillSuggested):
                    console.print(
                        f"[dim]💡 这个任务可能用到技能「{event.name}」："
                        f"{event.description}[/dim]"
                    )
                elif isinstance(event, TurnError):
                    logger.error("TurnError: %s", event.error)
        except (Exception, KeyboardInterrupt) as e:
            stop_status()
            if isinstance(e, KeyboardInterrupt):
                console.print("\n[dim]已中断。[/dim]")
                logger.info("用户中断第 %d 轮", turn_count)
            else:
                console.print(f"\n[red]出错了：{e}[/red]")
                logger.error("第 %d 轮异常: %s", turn_count, e, exc_info=True)
            continue
        finally:
            stop_status()

        console.print()
        reply = "".join(reply_parts)
        logger.info("第 %d 轮回复完成，长度: %d", turn_count, len(reply))

        # hot path 结束后，后台异步沉淀长期记忆
        longterm.add_async(config, user_input, reply)

        # 每 N 轮提示用户考虑复盘（不强制，只是把动作浮出水面）
        if turn_count % _CONSOLIDATE_REMINDER_EVERY == 0:
            console.print(
                f"[dim]💭 我们已经聊了 {turn_count} 轮，"
                "要不要运行 alfred consolidate 让管家复盘一下？[/dim]"
            )

    logger.info("会话结束: %s, 总轮数: %d", session.id, turn_count)
    console.print("[dim]再见。[/dim]")


def _remember(config, blocks: MemoryBlocks, content: str) -> None:
    """显式教学：把一条事实追加进 human 块。"""
    current = blocks.read("human")
    updated = current.rstrip() + f"\n- {content}\n"
    result = blocks.update("human", updated, reason=f"/remember: {content[:30]}")
    console.print(f"[green]{result}[/green]")


def _show_memory(config) -> None:
    items = longterm.list_all(config)
    if not items:
        console.print("[dim]长期记忆为空（或 mem0 未就绪）。[/dim]")
        return
    for m in items:
        console.print(f"  [{m.get('id', '?')[:8]}] {m.get('memory', m)}")


def _show_status(config) -> None:
    """在 chat 内显示当前 chat 模型与 embedding 的连接状态。"""
    from .llm import check_embed_connection, check_model_connection

    with console.status(f"[dim]测试 {config.models.chat} ...[/dim]"):
        chat_result = check_model_connection(config, config.models.chat)

    embed_label = f"embedding:{config.models.embed.provider}"
    with console.status(f"[dim]测试 {embed_label} ...[/dim]"):
        embed_result = check_embed_connection(config)

    chat_line = (
        f"[green]✓[/green]  连接正常  {chat_result['latency_ms']:.0f}ms"
        if chat_result["ok"]
        else f"[red]✗[/red]  {chat_result['error']}"
    )
    embed_line = (
        f"[green]✓[/green]  连接正常  {embed_result['latency_ms']:.0f}ms"
        if embed_result["ok"]
        else f"[red]✗[/red]  {embed_result['error']}"
    )
    ok = chat_result["ok"] and embed_result["ok"]
    console.print(Panel(
        f"当前模型：{config.models.chat}\n"
        f"  chat   {chat_line}\n"
        f"  embed  {embed_line}",
        title="[bold]连接状态[/bold]",
        border_style="green" if ok else "red",
    ))


def _show_lessons(config, arg=None) -> None:
    """显示 RefleXion 教训列表（按类别过滤）。"""
    from .memory.lessons import LessonsBlock

    lb = LessonsBlock(config)
    lessons = lb.list_lessons()
    if not lessons:
        console.print("[dim]还没有教训记录。运行 consolidate 从对话中自动提炼。[/dim]")
        return

    if arg:
        lessons = [l for l in lessons if arg.lower() in l.get("category", "").lower()]
        if not lessons:
            console.print("[dim]没有匹配该类别的教训。[/dim]")
            return

    console.print("[bold]教训记录（共 {} 条）[/bold]".format(len(lessons)))
    for i, l in enumerate(lessons, 1):
        cat = l.get("category", "?")
        title = l.get("title", "")
        console.print("  {}. [dim][{}][/dim] {}".format(i, cat, title))


def _show_whoami(config, blocks: MemoryBlocks) -> None:
    """显示 Alfred 的积累状态：记忆/教训/情景/笔记/框架。

    这是用户的长期资产仪表盘——让"Alfred 在成长"这件事看得见。
    """
    from .memory import longterm
    from .memory.lessons import LessonsBlock

    # ① 常驻记忆块
    human_text = blocks.read("human").strip()
    persona_text = blocks.read("persona").strip()
    human_nonempty = "human" if human_text and "还没有关于用户" not in human_text else "空"
    persona_nonempty = "persona" if persona_text and "还没有" not in persona_text else "空"

    # ② 长期记忆
    memories = longterm.list_all(config)
    memory_count = len(memories)

    # ③ 教训
    lb = LessonsBlock(config)
    lessons = lb.list_lessons()
    lesson_count = len(lessons)

    # ④ 情景记忆（LanceDB episodes 表）
    episode_count = 0
    try:
        from .knowledge import store as knowledge_store
        db = knowledge_store.get_db(config)
        if "episodes" in db.table_names():
            episode_count = len(db.open_table("episodes").to_list())
    except Exception:
        episode_count = 0

    # ⑤ 笔记索引（LanceDB notes 表）
    notes_count = 0
    try:
        db = knowledge_store.get_db(config)
        if "notes" in db.table_names():
            notes_count = len(db.open_table("notes").to_list())
    except Exception:
        notes_count = 0

    # ⑥ 思维框架（LanceDB frameworks 表）
    framework_count = 0
    try:
        db = knowledge_store.get_db(config)
        if "frameworks" in db.table_names():
            framework_count = len(db.open_table("frameworks").to_list())
    except Exception:
        framework_count = 0

    lines = [
        f"[bold]常驻记忆块[/bold]",
        f"  human   {'[green]✓[/green] 已建立' if human_nonempty == 'human' else '[dim]空[/dim]'}",
        f"  persona {'[green]✓[/green] 已建立' if persona_nonempty == 'persona' else '[dim]空[/dim]'}",
        "",
        f"[bold]长期记忆[/bold]    {memory_count} 条",
        f"[bold]RefleXion 教训[/bold]  {lesson_count} 条",
        f"[bold]情景记忆[/bold]    {episode_count} 条",
        f"[bold]笔记索引[/bold]    {notes_count} 个片段",
        f"[bold]思维框架[/bold]    {framework_count} 张卡片",
    ]

    total = memory_count + lesson_count + episode_count + notes_count + framework_count
    if total > 0:
        lines.append("")
        lines.append(f"[dim]Alfred 一共积累了 {total} 条数据。[/dim]")
    else:
        lines.append("")
        lines.append("[dim]Alfred 刚起步，还没什么积累。多聊、多喂书、多复盘，数据会自己长出来。[/dim]")

    console.print(Panel("\n".join(lines), title="[bold]Alfred 状态[/bold]", border_style="green"))


@app.command()
def ingest(notes_dir: Path = typer.Argument(..., help="笔记目录路径")):
    """索引笔记目录（增量）。首次运行会下载 embedding 模型（约 600MB）。"""
    from .knowledge.ingest import ingest as do_ingest

    config = load_config()
    with console.status("[dim]索引中（首次需下载 embedding 模型）…[/dim]"):
        stats = do_ingest(config, notes_dir)
    console.print(
        f"[green]完成：[/green]新增 {stats['added']} 篇，更新 {stats['updated']} 篇，"
        f"跳过 {stats['skipped']} 篇，共 {stats['chunks']} 个片段。"
    )


@app.command()
def feed(file: Path = typer.Argument(..., help="要喂养的书/文章文件")):
    """喂养一本书/一篇文章：提炼思维框架入库。"""
    from .knowledge.feed import feed as do_feed

    config = load_config()

    def _progress(done, total, found):
        console.print(f"  段落 {done}/{total}，提炼框架 {found} 个")

    stats = do_feed(config, file, progress=_progress)
    console.print(
        f"[green]完成：[/green]通读 {stats['segments']} 段，"
        f"入库 {stats['frameworks']} 个思维框架。"
    )
    if stats["names"]:
        console.print("提炼的框架：" + "、".join(stats["names"][:10]))


@app.command()
def frameworks(query: str = typer.Argument(..., help="检索词")):
    """检索已提炼的思维框架。"""
    from .knowledge.feed import search_frameworks

    config = load_config()
    results = search_frameworks(config, query)
    if not results:
        console.print("[dim]框架库为空或没有匹配。[/dim]")
        return
    for r in results:
        console.print(Panel(r["text"], title=r.get("name", "框架")))


@app.command()
def consolidate():
    """睡眠整理：复盘近期对话，产出记忆/规则草稿（确认后入库）。"""
    from .memory.consolidate import apply_drafts, generate_drafts

    config = load_config()
    with console.status("[dim]复盘中（使用记忆写入模型）…[/dim]"):
        drafts = generate_drafts(config)
    if not drafts:
        console.print("[dim]近期没有需要整理的对话。[/dim]")
        return
    if "error" in drafts:
        console.print(f"[red]{drafts['error']}[/red]\n{drafts.get('raw', '')}")
        return

    applied = apply_drafts(config, drafts, confirm=_confirm)
    if applied:
        console.print("[green]已应用：[/green]")
        for a in applied:
            console.print(f"  - {a}")
    else:
        console.print("[dim]没有应用任何草稿。[/dim]")


@app.command()
def memory(
    action: str = typer.Argument("list", help="list / delete / history"),
    target: str = typer.Argument(None, help="delete 时为记忆 id，history 时为块名"),
):
    """查看与管理记忆。"""
    config = load_config()
    if action == "list":
        _show_memory(config)
    elif action == "delete" and target:
        items = longterm.list_all(config)
        hit = next((m for m in items if str(m.get("id", "")).startswith(target)), None)
        if hit and Confirm.ask(f"删除记忆「{hit.get('memory', '')[:60]}」？", default=False):
            longterm.delete(config, hit["id"])
            console.print("[green]已删除。[/green]")
    elif action == "history":
        blocks = MemoryBlocks(config)
        for line in blocks.history(name=target):
            console.print(f"  {line}")
    else:
        console.print("用法：alfred memory list | delete <id> | history [human|persona]")


@app.command()
def skills():
    """列出已发现的技能与规则。"""
    from .rules.loader import scan_rules
    from .skills.loader import scan_skills

    config = load_config()
    found = scan_skills(config)
    console.print("[bold]技能：[/bold]")
    for s in found:
        console.print(f"  {s.name} — {s.description}")
    rules = scan_rules(config)
    console.print("[bold]规则：[/bold]")
    for r in rules:
        tag = "常驻" if r.always_apply else "按需召回"
        console.print(f"  {r.name} [{tag}] — {r.description}")


if __name__ == "__main__":
    app()
