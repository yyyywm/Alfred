# AGENTS.md

## 项目简介
Alfred —— 私人管家 AI Agent（Python 3.13，conda base 环境）。
核心：长期记忆 + 笔记 RAG + skills 扩展 + 自我成长。

## 环境
- 使用 conda base 环境：`source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh && conda activate base`
- 不使用 venv（已废弃）。安装：`pip install -e ".[dev]"`

## 命令
- 测试：`python -m pytest tests/ -q`
- 运行：`alfred chat` / `alfred models` / `alfred ingest <dir>` / `alfred feed <file>` / `alfred consolidate`

## 约定
- 配置驱动：`config.yaml` 是唯一模型配置入口，代码里不得硬编码模型名或 API 地址
- 记忆层与知识层严格分离：`alfred/memory/`（对用户的认知）vs `alfred/knowledge/`（笔记/框架）
- 记忆写入路径固定用 `models.memory_write` 强模型；闲聊路径才可切模型
- persona 修改与 shell/python 执行必须走 `deps.confirm` 用户确认（代码层强制）
- system prompt 组装顺序不可随意调整：静态 instructions → memory blocks → 动态层（KV-cache 纪律）
- 会话历史 append-only；压缩走 `compaction.py`，丢内容留指针
- 测试不依赖真实 LLM 调用与 embedding 下载，只测纯逻辑
- 提交规范：Conventional Commits（feat/fix/docs/test/chore/refactor），按逻辑单元分开提交
