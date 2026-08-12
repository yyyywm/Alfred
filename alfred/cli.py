"""CLI 入口：alfred chat / ingest / feed / consolidate / memory / models / skills。

chat 内斜杠命令：
  /exit 退出  /new 新会话  /model <provider:model> 切换闲聊模型
  /remember <内容> 显式教学（写入 human 块）
  /memory 查看长期记忆  /why 查看上一轮用了哪些记忆
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from .agent import AlfredDeps, build_agent, chat_turn
from .config import load_config
from .history import Session, list_sessions
from .memory import longterm
from .memory.blocks import MemoryBlocks

app = typer.Typer(help="私人管家 AI Agent", no_args_is_help=True)
console = Console()


def _confirm(msg: str) -> bool:
    console.print(Panel(msg, title="[yellow]确认请求[/yellow]", border_style="yellow"))
    return Confirm.ask("是否允许", default=False)


def _print_connection_result(ref: str, result: dict) -> None:
    if result["ok"]:
        console.print(f"  [green]✓[/green]  {ref}  {result['latency_ms']}ms")
    else:
        console.print(f"  [red]✗[/red]  {ref}  {result['error']}")


@app.command()
def models(
    model_ref: str | None = typer.Argument(None, help="要测试的 provider:model"),
    all_models: bool = typer.Option(False, "--all", help="测试所有配置的模型"),
):
    """列出配置的 provider 与模型，检查 key 可用性；可测试单个或全部模型连通性。"""
    from .llm import check_model_connection, list_models

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
        return

    if all_models and model_ref:
        console.print("[red]不能同时指定 model_ref 和 --all。[/red]")
        raise typer.Exit(1)

    if all_models:
        for ref, _ptype, _ready in rows:
            with console.status(f"[dim]测试 {ref} ...[/dim]"):
                result = check_model_connection(config, ref)
            _print_connection_result(ref, result)
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
        return


@app.command()
def chat(session_id: str = typer.Option(None, "--session", "-s", help="恢复指定会话")):
    """开始与管家对话。"""
    config = load_config()
    blocks = MemoryBlocks(config)
    session = Session(config, session_id=session_id)
    agent = build_agent(config)
    deps = AlfredDeps(config=config, blocks=blocks, confirm=_confirm)

    console.print(Panel(
        f"会话 {session.id} ｜ 模型 {config.models.chat} ｜ 输入 /exit 退出，/help 查看命令",
        title="[bold]私人管家[/bold]",
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]你[/bold cyan]").strip()
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
            elif cmd == "/model":
                if not arg:
                    console.print(f"当前模型：{config.models.chat}（切换：/model provider:model）")
                else:
                    try:
                        config.resolve(arg)
                        config.models.chat = arg
                        agent = build_agent(config, arg)
                        console.print(f"[green]已切换模型：{arg}[/green]")
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
            elif cmd == "/sessions":
                for sid, mtime, n in list_sessions(config)[:10]:
                    from datetime import datetime
                    console.print(f"  {sid}  {datetime.fromtimestamp(mtime):%m-%d %H:%M}  {n} 条消息")
            else:
                console.print(f"[red]未知命令 {cmd}，输入 /help 查看。[/red]")
            continue

        try:
            with console.status("[dim]思考中…[/dim]"):
                reply = chat_turn(agent, deps, session, user_input)
        except Exception as e:
            console.print(f"[red]出错了：{e}[/red]")
            continue
        console.print(Panel(Markdown(reply), title="[bold green]管家[/bold green]",
                            border_style="green"))

        # hot path 结束后，后台异步沉淀长期记忆
        longterm.add_async(config, user_input, reply)

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
