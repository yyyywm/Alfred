"""CLI 入口：alfred chat / ingest / feed / consolidate / memory / models / skills。

chat 内斜杠命令：
  /exit 退出  /new 新会话  /model <provider:model> 切换闲聊模型
  /remember <内容> 显式教学（写入 human 块）
  /memory 查看长期记忆  /why 查看上一轮用了哪些记忆
  /sessions 列出历史会话  /load <序号或id> 加载历史会话  /delete <序号或id> 删除会话
  /lessons 查看管家从过去中学到的教训（RefleXion 教训库）
  /trust 管理工具信任白名单（默认允许某类工具，不再每次询问）
  /whoami 查看 Alfred 的积累状态（记忆/教训/情景/笔记/框架）
  /status 检查当前模型与 embedding 连接
  /consolidate-review 查看自动复盘暂存的待审查草稿
"""

from __future__ import annotations

import asyncio
import logging
import shutil
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
)

from .agent import AlfredDeps, build_agent, chat_turn_stream
from ._stream_render import StreamMarkdown
from .config import load_config
from .history import Session, delete_session, list_sessions
from .memory import longterm
from .memory.blocks import MemoryBlocks
from .memory.lessons import LessonsBlock
from .skills.loader import render_skills_index, scan_skills
from .rules.loader import render_rules, scan_rules

app = typer.Typer(help="私人管家 AI Agent", no_args_is_help=True)
console = Console()

_ALFRED_VERSION = "0.1.0"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALFRED_LOGO = (
    "[bold]██████[/bold]  Welcome to Alfred!"
)
ALFRED_HELP_LINE = (
    "  Send /help for help information."
)


def _print_startup_banner(config: "Config", session_id: str, has_session: bool) -> None:
    """渲染类似 Kimi Code 的启动面板。Rich 自动处理宽度/对齐。"""
    session_text = session_id if has_session else "(will be created on your first message)"

    content = (
        f"\n"
        f"  {ALFRED_LOGO}\n"
        f"  {ALFRED_HELP_LINE}\n"
        f"\n"
        f"  Directory: {_PROJECT_ROOT}\n"
        f"  Session:   {session_text}\n"
        f"  Model:     {config.models.chat}\n"
        f"  Version:   {_ALFRED_VERSION}"
    )

    console.print()
    console.print(
        Panel(
            content,
            border_style="cyan",
            padding=(0, 1),
            width=96,
            expand=False,
        )
    )
    if not has_session:
        console.print(
            "\n[dim]  No session yet — one will be created on your first message.[/dim]\n"
        )

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


class _ConfirmState:
    """会话内工具信任状态。

    trusted_tools: 用户明确允许过、后续可自动放行的工具名集合。
    工具名来自 agent._confirm_prompt 中的四个危险工具：
    shell / run_python / code_patch / memory_update_block。

    语义：第一次允许某工具 → 该工具进入白名单 → 同会话内后续同类调用自动放行。
    用户在 /trust 中可手动增删白名单条目，也可清除全部。
    """
    trusted_tools: set[str] = set()


def _confirm(msg: str, tool_name: str | None = None) -> bool:
    """在工作线程中请求用户确认；避开 Rich Console 以减少线程竞争。

    若该工具已在白名单中，自动放行不再打扰用户。
    用户回答 y/yes → 该工具加入白名单；回答其他 → 保持现状（下次仍问）。
    """
    # 白名单内自动放行
    if tool_name is not None and tool_name in _ConfirmState.trusted_tools:
        return True
    print(f"\n{'=' * 60}\n确认请求\n{'=' * 60}\n{msg}\n{'=' * 60}")
    print("[dim]提示：回答 y 后本次会话内同类操作将自动放行（/trust 查看管理）[/dim]")
    answer = input("是否允许 [y/N]: ").strip().lower()
    allowed = answer in ("y", "yes")
    if allowed and tool_name is not None:
        _ConfirmState.trusted_tools.add(tool_name)
    return allowed


def _show_trust_state() -> None:
    """/trust 命令：显示白名单状态与帮助。"""
    from rich.table import Table

    tools_desc = {
        "shell": "shell 命令执行",
        "run_python": "Python 代码执行",
        "code_patch": "自举进化（修改源代码）",
        "memory_update_block": "修改记忆块（human/persona）",
    }
    table = Table(title="工具信任白名单（会话内有效）", show_header=False)
    for name in ("shell", "run_python", "code_patch", "memory_update_block"):
        desc = tools_desc[name]
        status = "[green]✓ 已信任[/green]" if name in _ConfirmState.trusted_tools else "[dim]未信任[/dim]"
        table.add_row(f"[bold]{name}[/bold]", desc, status)

    console.print(Panel(table))
    console.print("[dim]用法：[/dim]")
    console.print("  [green]/trust add <tool>[/green]  手动信任某类工具")
    console.print("  [red]/trust remove <tool>[/red]  移除信任，下次仍会询问")
    console.print("  [yellow]/trust clear[/yellow]     清空白名单，全部回到手动确认")
    console.print("  [cyan]/trust[/cyan]              查看当前状态（即此视图）")


def _handle_trust(arg: str) -> None:
    """/trust [add|remove|clear] <tool> — 管理工具信任白名单。"""
    _VALID_TOOLS = {"shell", "run_python", "code_patch", "memory_update_block"}

    parts = arg.split(maxsplit=1)
    if not parts:
        _show_trust_state()
        return

    sub = parts[0].lower()
    tool = parts[1] if len(parts) > 1 else ""

    if sub == "add":
        if not tool:
            console.print("用法：/trust add <tool>")
            return
        if tool not in _VALID_TOOLS:
            console.print(f"[red]未知工具：{tool}[/red]")
            return
        _ConfirmState.trusted_tools.add(tool)
        console.print(f"[green]已信任：{tool}[/green]（后续同类操作自动放行）")
    elif sub == "remove":
        if not tool:
            console.print("用法：/trust remove <tool>")
            return
        if tool not in _ConfirmState.trusted_tools:
            console.print(f"[dim]{tool} 未在白名单中。[/dim]")
            return
        _ConfirmState.trusted_tools.discard(tool)
        console.print(f"[yellow]已移除信任：{tool}[/yellow]（下次调用仍需确认）")
    elif sub == "clear":
        if not _ConfirmState.trusted_tools:
            console.print("[dim]白名单已为空。[/dim]")
            return
        _ConfirmState.trusted_tools.clear()
        console.print("[green]已清空白名单。[/green]")
    else:
        console.print(f"[red]未知子命令：/trust {sub}[/red]")
        return


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
    _always_rules, _recall_rules = render_rules(scan_rules(config))
    _rules_text = "\n\n".join(t for t in (_always_rules, _recall_rules) if t)
    deps = AlfredDeps(
        config=config,
        blocks=blocks,
        confirm=_confirm,
        skill_index=_skill_index,
        lessons_text=_lessons_text,
        rules_text=_rules_text,
    )
    logger = _setup_chat_logger(config, debug=debug)

    _print_startup_banner(config, session.id, has_session=bool(session_id))
    logger.info("会话开始: %s, 模型: %s, debug: %s", session.id, config.models.chat, debug)

    # 检查是否有历史 session 遗留的到期定时任务（未在当前 session 触发过），
    # 如果有，以首条用户输入的形式让 agent 主动处理
    from .schedule import schedule_fire_pending as _fire_pending
    fired_prompts = []
    try:
        fired_prompts = _fire_pending(config)
    except Exception as exc:
        logger.warning("启动时检查定时任务失败: %s", exc)
    if fired_prompts:
        combined = (
            "【系统提醒】以下定时任务已到期，请你主动处理：\n"
            + "\n".join(f"- {p}" for p in fired_prompts)
        )
        console.print(f"[bold cyan]⏰ {len(fired_prompts)} 条到期定时任务，将注入首条对话处理[/bold cyan]")
        # 把首条提示追加到 prompt_session 之前的输入，通过 user_input 注入下一轮
        # 简单做法：直接在下面 prompt 循环前注入一次
        _initial_injection = combined
    else:
        _initial_injection = None

    prompt_session = PromptSession(
        "你: ",
        style=Style.from_dict({"prompt": "cyan bold"}),
    )

    turn_count = 0
    # 每隔 N 轮对话，在回复后提示用户考虑复盘（让巩固动作浮出水面）
    _CONSOLIDATE_REMINDER_EVERY = 10
    listed_sessions: list[tuple[str, float, int]] = []
    while True:
        # 若启动时有到期定时任务，注入为"首条用户输入"自动处理
        if _initial_injection is not None:
            user_input = _initial_injection
            _initial_injection = None
            console.print(f"[dim]（定时任务自动处理中…）[/dim]")
        else:
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
                # 退出前提示待审查的草稿（不阻塞，自动 consolidate 走后台线程）
                try:
                    from .memory import consolidate_state
                    from .memory.consolidate_state import should_auto_consolidate
                    pending_path = config.path(config.paths.history_dir) / "consolidate_pending.jsonl"
                    has_pending = pending_path.exists() and pending_path.stat().st_size > 0
                    auto_will_run = should_auto_consolidate(config, turn_count)
                    if auto_will_run:
                        console.print(
                            "[dim]💤 满足自动复盘条件，将在后台运行 consolidate（稍后 /consolidate-review 可查看结果）。[/dim]"
                        )
                    elif has_pending:
                        console.print(
                            "[dim]💤 有待审查的草稿，运行 /consolidate-review 查看。[/dim]"
                        )
                except Exception as e:
                    logger.warning("退出前复盘检测失败: %s", e)
                break
            elif cmd == "/help":
                console.print(__doc__)
            elif cmd == "/new":
                session = Session(config)
                # 信任白名单声明为"会话内有效"，/new 后回到手动确认
                _ConfirmState.trusted_tools.clear()
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
            elif cmd == "/trust":
                _handle_trust(arg)
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
            elif cmd == "/consolidate-review":
                _show_consolidate_pending(config)
            elif cmd == "/consolidate":
                _run_chat_consolidate(config)
            elif cmd in ("/audit", "/audit?"):
                _run_audit(config)
            else:
                console.print(f"[red]未知命令 {cmd}，输入 /help 查看。[/red]")
            continue

        # 每轮对话前检查是否有到期未触发的定时任务——注入本轮上下文，
        # 让 agent 有机会主动处理（这是 Alfred "主动行为"的入口）
        pending_schedule_prompts = []
        try:
            from .schedule import schedule_fire_pending
            pending_schedule_prompts = schedule_fire_pending(config)
        except Exception as exc:
            logger.warning("检查定时任务失败: %s", exc)
        if pending_schedule_prompts:
            injected = (
                f"\n\n[系统：以下 {len(pending_schedule_prompts)} 条定时任务已到期，"
                f"请主动处理]\n"
                + "\n".join(f"- {p}" for p in pending_schedule_prompts)
            )
            user_input = user_input + injected
            for p in pending_schedule_prompts:
                console.print(f"[dim]⏰ 定时任务已到期：{p[:60]}[/dim]")

        # 分隔用户输入与助手输出
        console.print()
        turn_count += 1
        logger.info("第 %d 轮输入，长度: %d", turn_count, len(user_input))

        reply_parts: list[str] = []          # 整轮全部文本，供记忆沉淀
        total_chars = 0
        is_tty = console.is_terminal

        status = console.status("[bold green]助手正在思考...[/bold green]", spinner="dots")
        if is_tty:
            status.start()
        status_active = is_tty

        # 流式 markdown（保留模式）：每个 AssistantChunk 把当前段整体重渲染，擦除量 =
        # 上一帧精确行数（由 Rich capture 给出，CJK 自动换行 / 表格框线都算对），结构上
        # 不可能像 Rich Live 那样滚雪球——Live 靠自己记账「上一帧几行」，在此终端算成
        # ~0 → 不擦 → 每帧整段往下追加一份 = 爆屏。这里擦除量永远等于我们上次写下去的
        # 行数，不会错。超长段落（≥终端高）回锁定，close() 追加最终 markdown。详见
        # alfred._stream_render。
        stream: StreamMarkdown | None = None

        def stop_status() -> None:
            nonlocal status_active
            if status_active:
                status.stop()
                status_active = False

        def _emit(s: str) -> None:
            # 流式帧的裸字节出口：绕开 console 的渲染层，把光标控制序列直接落终端。
            console.file.write(s)
            console.file.flush()

        def open_stream() -> None:
            """首段正文到达：落「助手：」头并开启流式渲染区。"""
            nonlocal stream
            console.print("[bold green]助手：[/bold green]")
            console.file.flush()
            cols, rows = shutil.get_terminal_size((80, 24))
            stream = StreamMarkdown(
                console, _emit, term_width=cols, term_height=rows,
            )

        def close_stream() -> None:
            """把当前流式段落定为最终 markdown 进 scrollback；幂等。"""
            nonlocal stream
            if stream is not None:
                stream.close()
                stream = None

        try:
            for event in chat_turn_stream(agent, deps, session, user_input, bus=EventBus()):
                logger.debug("事件: %s", type(event).__name__)
                if isinstance(event, AssistantChunk):
                    reply_parts.append(event.delta)
                    total_chars += len(event.delta)
                    if is_tty:
                        if stream is None:
                            stop_status()
                            open_stream()
                        stream.update(event.delta)
                    else:
                        # 非 TTY（管道/重定向）：直接流式原文
                        console.out(event.delta, end="")
                elif isinstance(event, ToolCallStart):
                    close_stream()
                    if not is_tty and reply_parts:
                        console.print()  # 收尾非 TTY 流式原文行
                    console.print(f"[dim]🔧 {event.tool_name} ...[/dim]")
                    logger.info("工具调用: %s, args: %s", event.tool_name, event.args)
                elif isinstance(event, ToolCallEnd):
                    mark = "[green]✓[/green]" if not event.is_error else "[red]✗[/red]"
                    console.print(f"[dim]🔧 {event.tool_name} {mark}[/dim]")
                    logger.info("工具结束: %s, 错误: %s", event.tool_name, event.is_error)
                elif isinstance(event, ToolDenied):
                    console.print(f"[dim]🔧 {event.tool_name} [red]已拒绝[/red][/dim]")
                    logger.info("工具拒绝: %s", event.tool_name)
                elif isinstance(event, TurnEnd):
                    stop_status()
                    close_stream()
                    if not is_tty and reply_parts:
                        console.print()
                elif isinstance(event, TurnError):
                    logger.error("TurnError: %s", event.error)
        except (Exception, KeyboardInterrupt) as e:
            stop_status()
            close_stream()  # 异常/中断时把半截正文也落定，别丢
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

        # 记录对话轮数，供 consolidate_state 判断是否自动触发
        from .memory import consolidate_state
        consolidate_state.record_turn(config, session.id, turn_count)

        # 每 N 轮提示用户考虑复盘（不强制，只是把动作浮出水面）
        if turn_count % _CONSOLIDATE_REMINDER_EVERY == 0:
            console.print(
                f"[dim]💭 我们已经聊了 {turn_count} 轮，"
                "要不要运行 alfred consolidate 让管家复盘一下？[/dim]"
            )

    logger.info("会话结束: %s, 总轮数: %d", session.id, turn_count)

    # 自动触发 consolidate：满足条件时在后台运行无人值守模式
    # 用线程包装，避免 LLM 调用阻塞 /exit 退出
    try:
        from .memory import consolidate_state
        if consolidate_state.should_auto_consolidate(config, turn_count):
            logger.info("会话结束，满足自动复盘条件，后台启动无人值守 consolidate")
            import threading as _threading

            def _auto_consolidate_run() -> None:
                try:
                    from .memory.consolidate import apply_unattended, generate_drafts
                    drafts = generate_drafts(config)
                    if drafts and "error" not in drafts:
                        applied = apply_unattended(config, drafts)
                        if applied:
                            logger.info("无人值守 consolidate 已应用: %s", applied)
                        consolidate_state.record_consolidate(config)
                except Exception as exc:
                    logger.warning("无人值守 consolidate 失败: %s", exc)

            thread = _threading.Thread(target=_auto_consolidate_run, daemon=True)
            thread.start()
    except Exception as e:
        logger.warning("自动 consolidate 触发失败（不影响会话）: %s", e)

    console.print("[dim]再见。[/dim]")


def _run_audit(config) -> None:
    """在 chat 内运行记忆审计，输出富文本诊断报告。"""
    from .memory.audit import audit as _do_audit
    from .memory.audit import format_audit_human

    try:
        with console.status("[dim]审计中…[/dim]"):
            report = _do_audit(config)
        console.print(Markdown(format_audit_human(report)))
    except Exception as e:
        console.print(f"[red]审计失败：{e}[/red]")


def _run_chat_consolidate(config) -> None:
    """在 chat 内运行 consolidate 复盘。

    和 alfred consolidate CLI 命令等价，允许用户在对话中直接触发复盘。
    """
    from .memory.consolidate import apply_drafts, generate_drafts

    with console.status("[dim]复盘中…[/dim]"):
        drafts = generate_drafts(config)
    if not drafts:
        console.print("[dim]近期没有需要整理的对话。[/dim]")
        return
    if "error" in drafts:
        console.print(f"[red]{drafts['error']}[/red]")
        return

    applied = apply_drafts(config, drafts, confirm=_confirm)
    if applied:
        console.print("[green]已应用：[/green]")
        for a in applied:
            console.print(f"  - {a}")
    else:
        console.print("[dim]没有应用任何草稿。[/dim]")


def _show_consolidate_pending(config) -> None:
    """显示自动 consolidate 暂存待审查的草稿。"""
    from datetime import datetime as _dt
    import json as _json
    from pathlib import Path as _Path

    pending_path = _Path(config.path(config.paths.history_dir)) / "consolidate_pending.jsonl"
    if not pending_path.exists():
        console.print("[dim]没有待审查的草稿。[/dim]")
        return

    entries = []
    for line in pending_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        entries.append(_json.loads(line))

    if not entries:
        console.print("[dim]没有待审查的草稿。[/dim]")
        return

    console.print(f"[bold]待审查草稿（共 {len(entries)} 条）[/bold]")
    for idx, entry in enumerate(entries, 1):
        ts = _dt.fromtimestamp(entry["ts"])
        console.print(f"\n--- #{idx}  {ts:%m-%d %H:%M} ---")
        drafts = entry["drafts"]
        for entry_text in drafts.get("memory_entries") or []:
            console.print(f"  📝 记忆条目：{entry_text[:100]}")
        hu = drafts.get("human_block_update")
        if hu:
            console.print(f"  👤 human 块更新（{len(hu)} 字）：{hu[:120]}...")
        for sug in drafts.get("rule_suggestions") or []:
            console.print(
                f"  📋 规则建议「{sug.get('name')}」：{sug.get('reason', '')}"
            )
        for stale in drafts.get("stale_memories") or []:
            console.print(f"  🗑  过时记忆：{stale[:80]}")

    console.print("\n[dim]处理建议：[/dim]")
    console.print("  - 运行 [bold]alfred consolidate[/bold] 逐项确认应用")
    console.print("  - 或编辑 [dim]data/history/consolidate_pending.jsonl[/dim] 手动清理")


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
    # 新模板用 _（...）_ 占位符表示"未填充"，检查是否仍是纯模板
    human_is_placeholder = "_（" in human_text
    persona_is_placeholder = "_（" in persona_text
    human_nonempty = "human" if human_text and not human_is_placeholder else "空"
    persona_nonempty = "persona" if persona_text and not persona_is_placeholder else "空"

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
        from .knowledge.store import _table_names
        db = knowledge_store.get_db(config)
        if "episodes" in _table_names(db):
            episode_count = len(db.open_table("episodes").to_list())
    except Exception:
        episode_count = 0

    # ⑤ 笔记索引（LanceDB notes 表）
    notes_count = 0
    try:
        db = knowledge_store.get_db(config)
        if "notes" in _table_names(db):
            notes_count = len(db.open_table("notes").to_list())
    except Exception:
        notes_count = 0

    # ⑥ 思维框架（LanceDB frameworks 表）
    framework_count = 0
    try:
        db = knowledge_store.get_db(config)
        if "frameworks" in _table_names(db):
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
        if hit and typer.confirm(f"删除记忆「{hit.get('memory', '')[:60]}」？", default=False):
            longterm.delete(config, hit["id"])
            console.print("[green]已删除。[/green]")
    elif action == "history":
        blocks = MemoryBlocks(config)
        for line in blocks.history(name=target):
            console.print(f"  {line}")
    else:
        console.print("用法：alfred memory list | delete <id> | history [human|persona]")


@app.command()
def audit():
    """记忆审计：诊断记忆库健康度、工具调用趋势、冷笔记、死规则。"""
    config = load_config()
    _run_audit(config)


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
