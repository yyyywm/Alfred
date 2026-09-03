---
name: defensive-patterns
description: Alfred 工程经验沉淀：每次踩坑后补一条，避免重复。
alwaysApply: false
---

# Alfred Defensive Patterns

每条规则来源于真实踩坑。新增规则前先查本文件，不要重复造轮子。

## 1. 异常类必须有 `__init__`，不能只有类型注解
**来源**：自举 Phase 1/2 — `ToolDeniedError(reason: str)` 只有注解，
  `exc.reason` 在运行时抛 `AttributeError`，测试门禁拦截。
**规则**：自定义异常类必须显式定义 `__init__(self, ...)` 并 `self.xxx = xxx`。

## 2. 字符串匹配的 `in` vs `startswith` 要看关键词位置
**来源**：`_extract_error_lines` 用 `startswith(('error',...))` 漏掉了
  `ValueError`（以 `value` 开头），改用 `any(kw in stripped)`。
**规则**：关键词匹配用 `in`，只有当确认关键词在行首时才用 `startswith`。

## 3. Windows PTY 下 `prompt_toolkit` 不接受 stdin 管道和 cua-driver PTY
**来源**：自举测试驱动时，消息永远进不了 Alfred chat 会话。
**规则**：需要以非交互方式驱动 Alfred，用 `chat_turn_stream()` 直接调用 API，
  不要尝试通过 stdin/pty 喂给 `prompt_toolkit`。

## 4. `code_patch` 单轮硬上限 1 次
**来源**：自举 Phase 1 — 第 2 次调用被 `code_patch_count` 拦截。
**规则**：单轮 code_patch 只能改一个文件。需要多文件改造时分轮执行。

## 5. 测试门禁会真的拦住 bug 并自动回滚
**来源**：自举 Phase 2 首试被 `test_chat_turn_stream_emits_tool_denied`
  拦截，文件自动回滚。
**规则**：不要假设测试门禁是摆设。改动后必须通过。

## 6. 新工具注册必须 `agent.tool(_wrap_tool(fn, name))` 两处都加
**来源**：新增 goal/schedule/session_search 等工具，只定义函数不注册是无效代码。
**规则**：在 `build_agent` 中新增工具时，先定义函数再在 `agent.tool(...)` 区注册。

## 7. 工具函数返回 `message` 时，字符串必须与测试断言逐字一致
**来源**：deny reason 句号偏差，测试没拦住但行为不标准。
**规则**：用户可见字符串以测试为准；测试不覆盖时主动与旧版对齐。
## 8. 调研/实验产生的临时文件必须当场归档或删除，不留项目根目录
**来源**：Notion CLI 调研时下载的 ntn_*.md / notion_llms.txt 忘在项目根目录，
  被用户发现"怎么多了这么多文件"。
**规则**：下载的参考文档、临时导出、测试产物，用完立即移入 docs/ 对应子目录
  或删除；每次任务收尾时 `dir` 检查一遍根目录有没有自己留下的残渣。

## 9. Windows 上 Python 脚本 print 中文/符号前必须 reconfigure stdout 为 UTF-8
**来源**：run_python 和 notion_sync.py 两次踩坑——控制台是 GBK，
  print("✓"/中文) 直接 UnicodeEncodeError 把成功流程打断成假失败。
**规则**：任何要独立运行的脚本，开头固定加：
  `sys.stdout.reconfigure(encoding="utf-8")`（裹 try/except），
  或干脆只用 ASCII 符号（[OK]/[FAIL]）。
