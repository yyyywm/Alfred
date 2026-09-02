---
name: notion
description: 通过 Notion 官方 CLI（ntn）搜索、读取、创建、更新 Notion 页面与数据库
when-to-use: 用户要求查看、整理、写入 Notion，或提到 Notion 页面、数据库、看板、把笔记/总结同步到 Notion 时
---

# Notion 操作（ntn CLI）

通过 Notion 官方 CLI `ntn` 操作用户的 Notion workspace。所有调用走 Alfred 的 `shell` 工具（每次执行需用户确认，这是正常流程）。

## Procedure

1. **确认认证可用**：`ntn api v1/users/me`。失败则提示用户检查 `NOTION_API_TOKEN`（见 Required from User），不要尝试其他认证方式。
2. **定位目标**：
   - 用户给了 Notion 链接：URL 末段 32 位 hex 即 page/database id，直接提取使用。
   - 用户只给了名字：`ntn api v1/search --data '{"query":"<关键词>"}'` 搜索，把候选标题列给用户确认目标。
3. **读页面**：`ntn pages get <page-id>`（默认输出 Markdown，最省上下文；需要结构化数据时加 `--json`）。
4. **建页面**：`ntn pages create --parent page:<parent-id>`，Markdown 内容经 stdin 传入（见 Specifications）。
5. **改页面**：`ntn pages edit <page-id>`，Markdown 内容经 stdin 传入。
6. **查数据库**：`ntn datasources query <data-source-id> --filter '<json>' --json`。
7. **不确定 API 字段时自助查 schema**：`ntn api ls --json`、`ntn api <path> --spec`，不要凭记忆猜字段。

## Specifications

- **认证**：`NOTION_API_TOKEN` 环境变量（Alfred 启动时从 `.env` 加载，shell 子进程自动继承）。
- **复杂请求体一律走 stdin 文件**：先用 shell 把 JSON 写入临时文件（如 `data/tmp/ntn-body.json`），再 `ntn api v1/pages < data/tmp/ntn-body.json`。**不要**用 inline 字段语法（`=` / `:=` / `==` 的类型区分是官方文档承认的常见错误源）。
- **输出解析**：机器处理一律加 `--json`；只有"读页面内容进上下文"用默认 Markdown。
- **输出截断**：shell 工具输出上限 5000 字符。读长页面时用 `ntn pages get <id> | head -c 4000` 分段，或先 `--json` 只取需要的字段。

## Advice

- 优先用高层封装命令（`pages get/create/edit`、`datasources query`），只有它们覆盖不了时才退到 `ntn api` 裸调用。
- 写入前先向用户简述要写入的内容要点（确认框里只能看到命令，看不到完整内容）。
- 一次任务涉及多个页面时，先把计划（读哪些、写哪些）列出来再逐个执行，避免来回试探浪费确认次数。

## Forbidden Actions

- 禁止不带 `--yes` 执行 `ntn pages trash` 等破坏性命令（会卡在 TTY 等待）；删除类操作必须在用户明确同意后执行。
- 禁止把 `NOTION_API_TOKEN` 打印到输出、写进任何文件或拼进命令行参数。
- 禁止修改用户未指定的页面；搜索到多个候选时必须让用户选定，不得自行猜测写入目标。

## Required from User

首次使用前需一次性完成：

1. 安装 CLI：`curl -fsSL https://ntn.dev | bash`
2. 在 <https://www.notion.so/profile/integrations> 创建 internal integration，复制 token
3. 把 token 写入项目 `.env`：`NOTION_API_TOKEN=ntn_...`（或 `secret_...`）
4. 在 Notion 中把要操作的页面/数据库**分享给该 integration**（页面右上角 ··· → Connections），否则 API 看不到这些页面
