# 会话标题（Session Title）设计文档

日期：2026-09-09
状态：已确认（用户已批准设计）

## 背景与问题

会话历史以 `data/history/<session_id>.jsonl` 存储，`/sessions` 列表只显示 12 位随机 id + 时间 + 消息数。时间久了用户无法从 id 判断会话内容，难以找回上下文。

## 需求（已与用户确认）

1. 每个会话增加一个标题字段，概括会话内容。
2. 用户可手动修改标题。
3. 系统自动概括生成标题：**首轮对话结束后用 LLM 后台概括**（用户选定方案）。
4. 存量无标题会话：`/sessions` 回退显示首条用户消息截断片段，不写回存储（用户选定方案）。
5. 标题参与 `/load`、`/delete` 的解析（关键词匹配）（用户选定方案）。

## 方案选型

考虑过三种存储方案：

- **A. 旁路元数据文件 + 不动 `list_sessions` 签名**：改动面最小，但标题与会话信息分散两处，长期维护性差（用户明确指出易成"屎山"）。
- **B. `list_sessions` 升级为富对象（选定）**：`SessionInfo` dataclass 承载 id/mtime/count/title，单一数据源，下游（audit/consolidate/monitor）同步迁移到字段访问，更利于后续扩展与调试。
- **C. 标题写入会话 JSONL 内部**：破坏 append-only 纪律，改标题需重写文件，回归风险高，否决。

## 设计

### 数据模型

`alfred/history.py`：

```python
@dataclass
class SessionInfo:
    id: str
    mtime: float
    msg_count: int
    title: str | None        # None = 未设置
    title_auto: bool = True  # True=自动概括，False=用户手动设置
```

- `list_sessions(config) -> list[SessionInfo]`，仍按 mtime 倒序；内部读取 meta 文件并合并标题到 `SessionInfo`。
- 排除清单加入 `sessions_meta.json`（沿用 consolidate 元数据文件的排除模式）。

### 元数据存储

`data/history/sessions_meta.json`：

```json
{
  "<session_id>": {"title": "...", "auto": true, "updated_at": 1757390000.0}
}
```

- 整体读入内存（条目少，文件小）；写入用临时文件 + `os.replace` 原子替换。
- 新增函数：
  - `set_title(config, session_id, title, auto: bool) -> None`
  - `delete_session` 删除会话文件时同步删除 meta 条目。
- meta 文件损坏（JSON 解析失败）时按空表处理并记 warning，不影响会话功能。

### 标题来源与优先级

- **手动优先**：`/title` 设置的标题（`auto=False`）永不被自动概括覆盖。
- **自动概括**：仅当会话 `title` 为空时写入（写前重读 meta 检查，避免竞态覆盖手动标题）。

### 自动生成流程

- 触发点：`chat` 主循环中，首个助手回复完成后（会话恰好有 1 条用户消息且无标题）。
- 执行：后台 daemon 线程（沿用 `longterm.add_async` 的模式），用 `models.chat` 当前模型，输入首条用户消息 + 首条助手回复截断片段，要求概括 ≤15 字标题。
- 降级：LLM 调用失败静默跳过并记日志，不阻塞对话，不打印到终端（防止污染 prompt_toolkit 输入）。

### 展示

- `/sessions` 每行：`序号. 标题  id  MM-dd HH:mm  N 条消息（当前）`；无标题时回退显示首条用户消息截断（约 30 字符，只读展示，不写回）。
- chat 启动 banner 加载已有会话时显示标题。

### 查找

`_resolve_session_ref` 解析顺序：

1. 列表序号（数字）
2. id 前缀
3. 标题子串（大小写不敏感）

标题匹配多个会话时，列出候选 id + 标题，提示用 id 消歧。`/load`、`/delete` 共用该逻辑。

### 交互

- 新斜杠命令 `/title <新标题>`：设置当前会话标题（`auto=False`）。
- `/title` 无参数：显示当前会话标题。
- `/help` 帮助文本同步。

### 下游迁移

| 文件 | 现状 | 改动 |
|---|---|---|
| `alfred/memory/audit.py:112,149` | 解包 3 元组 | 改为 `SessionInfo` 字段访问 |
| `alfred/memory/consolidate.py:129` | `s[1] >= cutoff` | 改为 `s.mtime >= cutoff` |
| `alfred/memory/monitor.py:44` | 解包 3 元组 | 改为字段访问 |
| `alfred/cli.py` | 3 元组 | 改为 `SessionInfo`，合并标题展示与解析 |
| `tests/test_memory_history.py` | 断言 3 元组 | 更新为 `SessionInfo` |
| `tests/test_audit_window.py` | mock 返回 3 元组 | mock 返回 `SessionInfo` |

### 测试（纯逻辑，不碰真实 LLM）

新增/更新：

- meta 读写、删除会话时清理 meta 条目
- meta 文件损坏时的降级
- 自动标题不覆盖手动标题的优先级规则（生成函数注入 mock）
- 回退预览提取（首条用户消息截断）
- 标题匹配解析（含多匹配消歧）

## 文档同步

按开发约定 #2，同步更新 `README.md`（`/title` 命令、`/sessions` 新格式）与 `AGENTS.md`（`list_sessions` 返回类型变更、新增 `sessions_meta.json` 排除项、新斜杠命令）。

## 非目标（YAGNI）

- 不做全文搜索会话内容（已有 `session_search` 覆盖当前会话内搜索）。
- 不给存量会话批量 LLM 补生成标题（用户选定回退显示方案）。
- 标题不参与 compaction/consolidate 逻辑。
