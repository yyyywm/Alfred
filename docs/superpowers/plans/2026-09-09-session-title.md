# 会话标题（Session Title）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为会话增加标题字段：首轮对话后 LLM 自动概括生成，用户可用 `/title` 手动修改，`/sessions` 展示标题、`/load` `/delete` 支持按标题关键词匹配。

**Architecture:** 标题元数据存旁路文件 `data/history/sessions_meta.json`（注意：扩展名是 `.json`，`list_sessions` 的 `*.jsonl` glob 天然不会匹配它）；`list_sessions` 返回值从 3 元组升级为 `SessionInfo` dataclass 并内部合并标题，下游 audit/consolidate/monitor 同步迁移到字段访问。自动生成走后台 daemon 线程调用 `models.chat` 模型，手动标题永不被覆盖。

**Tech Stack:** Python 3.11+, pydantic-ai（标题概括 LLM 调用）, pytest（纯逻辑测试，不碰真实 LLM）。

**Spec:** `docs/superpowers/specs/2026-09-09-session-title-design.md`

---

### Task 1: 标题元数据存储原语（history.py）

**Files:**
- Modify: `alfred/history.py`
- Test: `tests/test_session_titles.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_titles.py`：

```python
"""会话标题：meta 存储、优先级、回退预览、标题匹配解析（纯逻辑，不碰真实 LLM）。"""

from alfred.config import Config
from alfred.history import Session, delete_session, get_title, set_title


def _cfg(tmp_path):
    return Config(
        memory={"dir": str(tmp_path / "mem")},
        paths={"history_dir": str(tmp_path / "hist")},
    )


def test_set_and_get_title(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("你好")
    assert get_title(cfg, s.id) is None
    set_title(cfg, s.id, "闲聊", auto=False)
    assert get_title(cfg, s.id) == "闲聊"


def test_delete_session_cleans_meta(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("要删除")
    set_title(cfg, s.id, "临时", auto=False)
    assert delete_session(cfg, s.id) is True
    assert get_title(cfg, s.id) is None


def test_corrupt_meta_degrades_to_none(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    meta_file = cfg.path(cfg.paths.history_dir) / "sessions_meta.json"
    meta_file.write_text("{not json", encoding="utf-8")
    assert get_title(cfg, s.id) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_titles.py -v`
Expected: FAIL（`ImportError: cannot import name 'get_title' from 'alfred.history'`）

- [ ] **Step 3: 实现 meta 存储**

在 `alfred/history.py` 顶部 import 区加 `import logging`、`import os`（`json`/`time`/`Path` 已有），并在 `delete_session` 之前加入：

```python
SESSIONS_META_FILE = "sessions_meta.json"


def _meta_path(config: Config) -> Path:
    return config.path(config.paths.history_dir) / SESSIONS_META_FILE


def _load_meta(config: Config) -> dict:
    """读 sessions_meta.json；文件损坏按空表处理，不影响会话功能。"""
    f = _meta_path(config)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logging.getLogger(__name__).warning("sessions_meta.json 损坏，按空表处理: %s", e)
        return {}


def _save_meta(config: Config, meta: dict) -> None:
    f = _meta_path(config)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def set_title(config: Config, session_id: str, title: str, auto: bool) -> None:
    """设置会话标题。auto=True 表示自动概括，False 表示用户手动设置。"""
    meta = _load_meta(config)
    meta[session_id] = {"title": title, "auto": auto, "updated_at": time.time()}
    _save_meta(config, meta)


def get_title(config: Config, session_id: str) -> str | None:
    entry = _load_meta(config).get(session_id)
    return entry["title"] if entry else None
```

把 `delete_session` 改为删除会话文件时同步清理 meta（保持原有 True/False 返回语义）：

```python
def delete_session(config: Config, session_id: str) -> bool:
    """删除指定会话的历史文件与标题元数据。返回是否删除成功。"""
    safe_id = Path(session_id).name  # 防路径穿越
    f = config.path(config.paths.history_dir) / f"{safe_id}.jsonl"
    if not f.exists():
        return False
    f.unlink()
    meta = _load_meta(config)
    if safe_id in meta:
        del meta[safe_id]
        _save_meta(config, meta)
    return True
```

注意：`sessions_meta.json` 是 `.json`，`list_sessions` 的 `*.jsonl` glob 天然不匹配，无需加入排除清单（排除清单只针对 `.jsonl` 元数据文件）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_titles.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add alfred/history.py tests/test_session_titles.py
git commit -m "feat: add session title metadata store"
```

---

### Task 2: SessionInfo 迁移（list_sessions + 全部下游 + 既有测试）

`list_sessions` 返回类型变更会同时打破 audit/consolidate/monitor 三个下游和既有测试，必须作为一个原子提交完成，保证每次 commit 测试全绿。

**Files:**
- Modify: `alfred/history.py`（`SessionInfo`、`list_sessions`、新增 `session_preview`）
- Modify: `alfred/memory/audit.py:112,149-155`
- Modify: `alfred/memory/consolidate.py:129-131`
- Modify: `alfred/memory/monitor.py:44-52`
- Test: `tests/test_memory_history.py:136-158`、`tests/test_audit_window.py:27-34`、`tests/test_session_titles.py`（追加）

- [ ] **Step 1: 写失败测试（追加到 tests/test_session_titles.py）**

```python
def test_list_sessions_merges_title(tmp_path):
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("讨论记账软件")
    set_title(cfg, s.id, "记账软件选型", auto=True)
    (info,) = list_sessions(cfg)
    assert info.id == s.id
    assert info.title == "记账软件选型"
    assert info.title_auto is True
    assert info.msg_count == 1


def test_sessions_meta_file_not_listed(tmp_path):
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    set_title(cfg, s.id, "标题", auto=True)
    ids = {i.id for i in list_sessions(cfg)}
    assert ids == {s.id}


def test_corrupt_meta_list_sessions_title_none(tmp_path):
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    meta_file = cfg.path(cfg.paths.history_dir) / "sessions_meta.json"
    meta_file.write_text("{not json", encoding="utf-8")
    (info,) = list_sessions(cfg)
    assert info.title is None


def test_session_preview_fallback(tmp_path):
    from alfred.history import session_preview
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("帮我分析一下最近很火的 AI 编程工具")
    assert session_preview(cfg, s.id, max_chars=10) == "帮我分析一下最近很火…"
    assert session_preview(cfg, s.id, max_chars=100) == "帮我分析一下最近很火的 AI 编程工具"
    # 无用户消息 / 会话不存在 → None
    s2 = Session(cfg)
    assert session_preview(cfg, s2.id) is None
    assert session_preview(cfg, "nonexistent") is None
```

同时更新既有测试对新返回类型。`tests/test_memory_history.py` 的 `test_list_sessions_excludes_meta_files`（136-158 行）中两处解包改为字段访问：

```python
    sessions = list_sessions(cfg)
    ids = {i.id for i in sessions}
    assert ids == {s.id}
    # 且 meta 文件不会导致下游解析崩溃
    for i in sessions:
        sess = Session(cfg, session_id=i.id)
        assert len(sess.messages) == 1
```

`tests/test_audit_window.py` 的 `_ROWS`（27-34 行）改为 `SessionInfo`：

```python
from alfred.history import SessionInfo

_NOW = time.time()
_ROWS = [
    SessionInfo(id="recent", mtime=_NOW - 86400, msg_count=1),
    SessionInfo(id="stale", mtime=_NOW - 30 * 86400, msg_count=1),
]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_titles.py tests/test_memory_history.py tests/test_audit_window.py -v`
Expected: 新测试 FAIL（`list_sessions` 仍返回 3 元组 / `session_preview` 不存在），`test_audit_window` 因 `_scan_sessions` 解包 `SessionInfo` 失败。

- [ ] **Step 3: 实现 SessionInfo 与 list_sessions 迁移**

`alfred/history.py`，在 `Session` 类之前加：

```python
@dataclass
class SessionInfo:
    """list_sessions 返回的会话摘要（含标题元数据）。"""

    id: str
    mtime: float
    msg_count: int
    title: str | None = None       # None = 未设置标题
    title_auto: bool = True        # True=自动概括，False=用户手动设置
```

`list_sessions` 整体替换为：

```python
def list_sessions(config: Config) -> list[SessionInfo]:
    """返回会话摘要列表（含标题），按最近修改排序。

    排除非会话 JSONL（consolidate 元数据/待审草稿），否则会被下游当成
    会话历史解析而崩溃（drafts 字段不在 Message schema 内）。
    sessions_meta.json 是 .json 后缀，*.jsonl glob 天然不匹配。
    """
    d = config.path(config.paths.history_dir)
    if not d.exists():
        return []
    meta_files = {"consolidate_state.jsonl", "consolidate_pending.jsonl"}
    meta = _load_meta(config)
    rows = []
    for f in d.glob("*.jsonl"):
        if f.name in meta_files:
            continue
        count = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip() and '"type": "llm_state"' not in line:
                count += 1
        entry = meta.get(f.stem) or {}
        rows.append(SessionInfo(
            id=f.stem,
            mtime=f.stat().st_mtime,
            msg_count=count,
            title=entry.get("title"),
            title_auto=entry.get("auto", True),
        ))
    return sorted(rows, key=lambda r: r.mtime, reverse=True)
```

新增回退预览函数（放 `delete_session` 之后）：

```python
def session_preview(config: Config, session_id: str, max_chars: int = 30) -> str | None:
    """无标题会话的回退展示：首条用户消息截断（只读，不写回存储）。"""
    safe_id = Path(session_id).name
    f = config.path(config.paths.history_dir) / f"{safe_id}.jsonl"
    if not f.exists():
        return None
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip() or '"type": "llm_state"' in line:
                continue
            record = json.loads(line)
            if record.get("role") == "user":
                text = record.get("content", "").strip().replace("\n", " ")
                if not text:
                    return None
                return text[:max_chars] + "…" if len(text) > max_chars else text
    except (json.JSONDecodeError, OSError):
        return None
    return None
```

下游迁移——`alfred/memory/audit.py:112` 起：

```python
    for info in list_sessions(config):
        if cutoff and info.mtime < cutoff:
            continue
        try:
            s = Session(config, session_id=info.id)
        except Exception:
            continue
```

`alfred/memory/audit.py:149-155`：

```python
    sessions = list_sessions(config)
    report.total_sessions = len(sessions)
    report.total_messages = sum(i.msg_count for i in sessions)

    # 过滤到 days 范围内的会话，统计轮数
    recent_sessions = [i for i in sessions if i.mtime >= cutoff]
    report.total_turns = sum(i.msg_count for i in recent_sessions)
```

`alfred/memory/consolidate.py:129-135`（整个循环体都要换，第 135 行也用到 `sid`）：

```python
    sessions = [i for i in list_sessions(config) if i.mtime >= cutoff][:max_sessions]
    parts = []
    for info in sessions:
        s = Session(config, session_id=info.id)
        t = s.transcript()
        if t:
            parts.append(f"=== 会话 {info.id} ===\n{t}")
```

`alfred/memory/monitor.py:44-52`：

```python
    for info in list_sessions(config):
        if info.mtime < cutoff:
            continue
        try:
            s = Session(config, session_id=info.id)
        except Exception:
            continue

        day_key = datetime.fromtimestamp(info.mtime).strftime("%Y-%m-%d")
```

- [ ] **Step 4: 跑全部测试确认通过**

Run: `python -m pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add alfred/history.py alfred/memory/audit.py alfred/memory/consolidate.py alfred/memory/monitor.py tests/test_memory_history.py tests/test_audit_window.py tests/test_session_titles.py
git commit -m "feat: list_sessions returns SessionInfo carrying title metadata"
```

---

### Task 3: 自动标题生成模块（alfred/titles.py）

**Files:**
- Create: `alfred/titles.py`
- Test: `tests/test_session_titles.py`（追加）

- [ ] **Step 1: 写失败测试（追加到 tests/test_session_titles.py）**

```python
def test_clean_title():
    from alfred.titles import _clean_title
    assert _clean_title("记账软件选型") == "记账软件选型"
    assert _clean_title('  "记账软件选型"。\n多余行') == "记账软件选型"
    assert _clean_title("「读书计划」") == "读书计划"
    assert _clean_title("") is None
    assert _clean_title("   \n  ") is None
    assert len(_clean_title("一" * 50)) == 30  # 超长截断


def test_auto_title_never_overrides(tmp_path):
    from alfred.titles import write_auto_title
    cfg = _cfg(tmp_path)
    # 手动标题优先：自动概括不得覆盖
    s = Session(cfg)
    s.add_user("内容")
    set_title(cfg, s.id, "手动标题", auto=False)
    assert write_auto_title(cfg, s.id, "自动标题") is False
    assert get_title(cfg, s.id) == "手动标题"
    # 无标题时可以写入
    s2 = Session(cfg)
    s2.add_user("另一个会话")
    assert write_auto_title(cfg, s2.id, "自动标题") is True
    assert get_title(cfg, s2.id) == "自动标题"
    # 已有自动标题也不再重复覆盖
    assert write_auto_title(cfg, s2.id, "又来一个") is False
    assert get_title(cfg, s2.id) == "自动标题"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_titles.py -v`
Expected: FAIL（`ModuleNotFoundError: alfred.titles`）

- [ ] **Step 3: 实现 alfred/titles.py**

```python
"""会话标题的自动概括：首轮对话后后台生成，手动标题优先。

设计要点：
- 生成走 models.chat 当前模型，后台 daemon 线程执行，失败静默降级记日志，
  不阻塞对话、不打印到终端（避免污染 prompt_toolkit 输入）。
- write_auto_title 写前重读 meta 检查：手动标题（auto=False）永不被覆盖。
"""

from __future__ import annotations

import logging
import threading

from .config import Config
from .history import get_title, set_title

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
    title = text.splitlines()[0].strip().strip("\"'「」。")
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
        raw = getattr(result, "output", None) or result.data
        return _clean_title(raw)
    except Exception as e:
        logger.warning("自动生成会话标题失败: %s", e)
        return None


def write_auto_title(config: Config, session_id: str, title: str) -> bool:
    """仅当会话尚无标题时写入自动标题；返回是否写入。"""
    if get_title(config, session_id) is not None:
        return False
    set_title(config, session_id, title, auto=True)
    return True


def maybe_generate_title_async(
    config: Config, session_id: str, user_text: str, assistant_text: str
) -> None:
    """后台线程生成并写入标题；任何失败静默降级。"""

    def _run() -> None:
        title = generate_title(config, user_text, assistant_text)
        if title:
            write_auto_title(config, session_id, title)

    threading.Thread(target=_run, daemon=True).start()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_titles.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add alfred/titles.py tests/test_session_titles.py
git commit -m "feat: auto session title generation module"
```

---

### Task 4: CLI 接线（/title、/sessions 展示、标题匹配、banner、自动生成钩子）

**Files:**
- Modify: `alfred/cli.py`（docstring 帮助、import、`_print_startup_banner`、`_resolve_session_ref`、`/sessions`、`/load`、`/title`、chat 主循环钩子）
- Test: `tests/test_session_titles.py`（追加标题匹配解析测试）

- [ ] **Step 1: 写失败测试（追加到 tests/test_session_titles.py）**

```python
def test_resolve_session_ref_by_title(tmp_path):
    from alfred.cli import _resolve_session_ref
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s1 = Session(cfg)
    s1.add_user("a")
    set_title(cfg, s1.id, "记账讨论", auto=False)
    s2 = Session(cfg)
    s2.add_user("b")
    set_title(cfg, s2.id, "读书计划", auto=False)
    s3 = Session(cfg)
    s3.add_user("c")
    set_title(cfg, s3.id, "健身计划", auto=False)

    listed = list_sessions(cfg)
    # 序号
    assert _resolve_session_ref(cfg, "1", listed) == listed[0].id
    # id 前缀
    assert _resolve_session_ref(cfg, s1.id[:6], listed) == s1.id
    # 标题唯一匹配（大小写不敏感）
    assert _resolve_session_ref(cfg, "记账", listed) == s1.id
    # 标题多匹配 → None（CLI 侧会列出候选）
    assert _resolve_session_ref(cfg, "计划", listed) is None
    # 都不匹配 → None
    assert _resolve_session_ref(cfg, "不存在", listed) is None
```

注意：`from alfred.cli import _resolve_session_ref` 会触发 cli 模块级代码（Windows 事件循环策略、stdout 重配置），均有异常兜底，pytest 下安全。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_titles.py::test_resolve_session_ref_by_title -v`
Expected: FAIL（`_resolve_session_ref` 对 3 元组解包报错或标题匹配不存在）

- [ ] **Step 3: 实现 CLI 改动**

`alfred/cli.py` 第 63 行 import 改为：

```python
from .history import (
    Session,
    SessionInfo,
    delete_session,
    get_title,
    list_sessions,
    session_preview,
    set_title,
)
```

模块 docstring（第 7 行附近）帮助文本加一行：

```
  /sessions 列出历史会话（含标题）  /load <序号|id|标题关键词> 加载  /delete 同上
  /title <标题> 设置当前会话标题（无参数查看）
```

`_print_startup_banner`（84-112 行）签名与 session_text 改为：

```python
def _print_startup_banner(
    config: "Config", session_id: str, has_session: bool, title: str | None = None
) -> None:
    """渲染类似 Kimi Code 的启动面板。Rich 自动处理宽度/对齐。"""
    session_text = session_id if has_session else "(will be created on your first message)"
    if has_session and title:
        session_text = f"{session_id}  ({title})"
```

调用处（417 行）：

```python
    _print_startup_banner(
        config, session.id, has_session=bool(session_id),
        title=get_title(config, session.id) if session_id else None,
    )
```

`_resolve_session_ref`（225-233 行）整体替换：

```python
def _resolve_session_ref(config, ref: str, listed: list[SessionInfo]) -> str | None:
    """把 /load、/delete 的参数解析为会话 id：列表序号 → id 前缀 → 标题子串。"""
    if ref.isdigit():
        idx = int(ref) - 1
        return listed[idx].id if 0 <= idx < len(listed) else None
    sessions = list_sessions(config)
    for info in sessions:
        if info.id.startswith(ref):
            return info.id
    matches = [i for i in sessions if i.title and ref.lower() in i.title.lower()]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        console.print(f"[yellow]「{ref}」匹配到多个会话，请用 id 消歧：[/yellow]")
        for i in matches:
            console.print(f"  {i.id}  {i.title}")
    return None
```

`/sessions` 分支（529-537 行）替换为：

```python
            elif cmd == "/sessions":
                listed_sessions = list_sessions(config)[:10]
                if not listed_sessions:
                    console.print("[dim]还没有历史会话。[/dim]")
                for i, info in enumerate(listed_sessions, 1):
                    current = "（当前）" if info.id == session.id else ""
                    display = info.title or session_preview(config, info.id) or "(空会话)"
                    console.print(
                        f"  {i}. {display}  [dim]{info.id}[/dim]  "
                        f"{datetime.fromtimestamp(info.mtime):%m-%d %H:%M}  "
                        f"{info.msg_count} 条消息{current}"
                    )
```

`/load` 成功提示（550-552 行）加标题：

```python
                        loaded_title = get_title(config, sid)
                        title_part = f"（{loaded_title}）" if loaded_title else ""
                        console.print(
                            f"[green]已加载会话 {sid}{title_part}（{len(session.messages)} 条消息），继续之前的上下文。[/green]"
                        )
```

`/title` 新分支（放在 `/sessions` 分支之前）：

```python
            elif cmd == "/title":
                if not arg:
                    current_title = get_title(config, session.id)
                    console.print(
                        f"当前会话标题：{current_title or '(未设置)'}"
                        "（修改：/title <新标题>）"
                    )
                else:
                    set_title(config, session.id, arg, auto=False)
                    console.print(f"[green]已设置会话标题：{arg}[/green]")
                    logger.info("设置会话标题: %s -> %s", session.id, arg)
```

`listed_sessions` 类型注解（450 行）改为：

```python
    listed_sessions: list[SessionInfo] = []
```

自动生成钩子——在 450 行附近（`listed_sessions` 声明旁）加：

```python
    _title_attempted: set[str] = set()  # 本次运行已触发过标题生成的会话
```

在 `longterm.add_async(config, user_input, reply)`（707 行）之后加：

```python
        # 首个无标题会话的首轮完成后，后台自动生成标题（手动标题优先，失败静默）
        if session.id not in _title_attempted and get_title(config, session.id) is None:
            _title_attempted.add(session.id)
            from . import titles
            titles.maybe_generate_title_async(config, session.id, user_input, reply)
```

- [ ] **Step 4: 跑全部测试确认通过**

Run: `python -m pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 5: 手动冒烟（可选但建议）**

```bash
alfred chat   # 发一条消息，/sessions 查看标题是否生成；/title 测试手动修改；/load 用标题关键词测试
```

- [ ] **Step 6: Commit**

```bash
git add alfred/cli.py tests/test_session_titles.py
git commit -m "feat: /title command, title display and title-based session lookup"
```

---

### Task 5: 文档同步（README.md + AGENTS.md）

按开发约定 #2，功能变更必须同步文档。

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: 更新 README.md**

在斜杠命令说明处加 `/title`；`/sessions` 描述改为"列出历史会话（含标题，无标题时回退显示首条消息预览）"；说明 `/load`、`/delete` 支持序号 / id 前缀 / 标题关键词。

- [ ] **Step 2: 更新 AGENTS.md**

- `history.py` 职责描述：补充 `SessionInfo`、标题元数据存 `sessions_meta.json`（`.json` 后缀，`*.jsonl` glob 天然不匹配，不在排除清单内）、`set_title`/`get_title`/`session_preview`。
- 斜杠命令列表加 `/title`。
- "常见坑点"补一条：手动标题（auto=False）永不被自动概括覆盖；自动生成在首轮对话后后台线程执行、失败静默。

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: session title feature"
```

---

## 自查记录

- Spec 覆盖：数据模型（Task 1-2）、标题来源与优先级（Task 3）、自动生成（Task 3+4 钩子）、展示（Task 4 `/sessions`+banner）、查找（Task 4 `_resolve_session_ref`）、交互 `/title`（Task 4）、下游迁移（Task 2）、测试（每个 Task）、文档（Task 5）。存量会话回退显示 = `session_preview`（Task 2 实现，Task 4 接线）。无遗漏。
- 非目标遵守：未做全文搜索、未做存量批量补生成。
