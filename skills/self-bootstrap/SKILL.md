---
name: self-bootstrap
description: Alfred 自举进化操作规范：code_patch 工具驱动的自修改流程。
triggers:
  - 自举
  - 自修改
  - code_patch
  - 代码进化
---

# 自举进化操作规范

## 适用范围
当需要修改 `alfred/` 下源代码但不通过外部工具时，走 code_patch 自举路径。

## 硬约束（不可违反）
1. **单轮最多 1 次 code_patch**：`AlfredDeps.code_patch_count` 硬拦截
2. **路径门禁**：只允许 `alfred/` 和 `config.yaml`
3. **语法门禁**：`py_compile` 必须通过
4. **测试门禁**：`pytest` 必须全过（当前 81 项）
5. **`tests/` 不可写**：防止 agent 自证清白
6. **`_wrap_tool(fn, name)` 签名不变**：被 `test_agent_loop.py` 直接 import

## 流程
1. **设计阶段**：人工（人类）设计改造方案，写成任务卡（`.md` 文件）
2. **任务卡放到 `data/prompts/`**：Alfred 用 `file_read` 读取
3. **Alfred 执行 code_patch**：每次只改一处
4. **人工验证**：
   - 读回修改后的文件检查代码质量
   - 跑 `pytest` 确认全过
   - 如被门禁拦截，Alfred 会自诊断根因
5. **多轮迭代**：每轮一个 code_patch，按任务卡分步执行

## 已知坑点
- 自定义异常类必须 `__init__` 显式赋值属性（`exc.xxx` 才有效）
- Windows PTY 下不能通过 prompt_toolkit 喂消息，用 `chat_turn_stream()` API
- 新工具函数定义后必须 `agent.tool(_wrap_tool(fn, name))` 注册

## 什么时候不要自举
- 涉及 pyproject.toml / 依赖变更 → 人工改
- 跨多文件架构级改造 → 人工分批 patch
- 不确定设计 → 人工评审后再让 Alfred 执行