# AGENTS.md

> 本文件面向 AI 编程助手。阅读前默认不了解本项目；所有信息基于当前仓库实际内容，而非假设。

## 项目概览

**Alfred** 是一个以"长期记忆 + 个人知识库"为核心的私人 AI 管家（Python CLI 应用）。

核心能力：
- **长期记忆**：跨会话记住用户的经历、偏好、思维方式
- **笔记 RAG**：索引个人 Markdown 笔记库，问答时引用出处
- **喂养学习**：通读书籍/文章，提炼成思维框架卡片入库
- **情景记忆**：记录成功经验四元组（场景/思路/行动/结果），类似场景自动召回
- **RefleXion 教训机制**：从工具失败/用户纠正中提炼教训，下次类似场景自动激活
- **自举进化**：`code_patch` 工具允许在人类监督下修改自身源代码（CodeAct + SWE-bench）
- **自我成长**：定期 `consolidate` 复盘对话，沉淀记忆、规则、教训草稿
- **模型无关**：通过声明式 provider 配置接入任意 OpenAI / Anthropic / Gemini / 本地兼容端点

技术选型：Python 3.11+，Pydantic AI 作为 agent 内核，mem0 作为长期记忆层，LanceDB 作为向量存储，sentence-transformers 跑本地 embedding，Typer + Rich 做 CLI。

## 环境要求

- Python >= 3.11
- 依赖管理：`pyproject.toml` + `pip`，同时提供 `environment.yml` 供 conda 用户直接使用
- 支持 conda 环境（含 web 版 conda），也兼容原生 pip 安装

## 安装

### 使用 conda（推荐，含 web 版 conda 环境）

```bash
conda env create -f environment.yml
conda activate alfred
cp .env.example .env   # 填入至少一个 LLM API key
```

### 使用 pip

```bash
pip install -e ".[dev]"
cp .env.example .env   # 填入至少一个 LLM API key
```

首次运行 `alfred ingest` 或 `alfred chat` 时，会按需下载 embedding 模型（约 600MB）。

## 构建与测试命令

```bash
# 运行测试（不依赖真实 LLM 调用与 embedding 下载，只测纯逻辑）
python -m pytest tests/ -q

# 检查模型与 key 配置
alfred models

# 开始对话
alfred chat

# 索引笔记目录（增量）
alfred ingest <notes_dir>

# 喂养一本书/文章，提炼思维框架
alfred feed <file.md>

# 睡眠整理：复盘近期对话，产出草稿待确认
alfred consolidate

# 查看/管理长期记忆
alfred memory list
alfred memory delete <id_prefix>
alfred memory history [human|persona]

# 查看已发现的技能与规则
alfred skills

# 检索已提炼的思维框架
alfred frameworks <query>
```

## 项目结构

```
Alfred/
├── pyproject.toml          # 包元数据、依赖、entry point
├── config.yaml             # 唯一模型/路径配置入口（代码不得硬编码模型）
├── .env.example            # API key 模板
├── README.md               # 用户文档
├── AGENTS.md               # 本文件
│
├── alfred/                 # 核心包
│   ├── __init__.py
│   ├── cli.py              # Typer CLI 入口与所有子命令
│   ├── config.py           # 配置模型与加载（Pydantic + YAML + .env）
│   ├── llm.py              # provider:model → pydantic-ai model 实例
│   ├── agent.py            # agent 内核：四层 prompt + 恒定工具集
│   ├── events.py           # 事件总线： TurnStart/AssistantChunk/ToolCall*/TurnEnd 等
│   ├── history.py          # 会话历史 JSONL 归一化持久化
│   ├── compaction.py       # 上下文压缩：丢内容留指针 + 偏好优先
│   ├── codewriting.py      # 自举进化：code_patch 三重门禁（路径/语法/测试）
│   │
│   ├── memory/             # 记忆层（agent 对用户的认知）
│   │   ├── protocols.py    # MemoryClient / EmbeddingClient 协议接口
│   │   ├── local.py        # 本地 MemoryClient 实现（mem0 + Qdrant）
│   │   ├── blocks.py       # human/persona 常驻记忆块 + git 版本化
│   │   ├── lessons.py      # RefleXion 教训记忆块（追加型，git 版本化）
│   │   ├── longterm.py     # 长期记忆 client 工厂（provider 可插拔）
│   │   ├── recall.py       # 混合召回：预算控制 + 相关性/近因排序
│   │   ├── episodic.py     # 情景记忆：成功案例四元组库
│   │   └── consolidate.py  # sleep-time 整理：复盘 → 草稿 → 确认入库
│   │
│   ├── knowledge/          # 知识层（用户笔记与喂养材料）
│   │   ├── chunking.py     # Markdown 标题层级切分 + frontmatter
│   │   ├── embed.py        # 本地/云端 embedding 模型封装
│   │   ├── store.py        # LanceDB 向量存储（notes/frameworks/episodes）
│   │   ├── ingest.py       # 笔记增量索引管线
│   │   └── feed.py         # 喂书管线：分段通读 → 框架卡片 → 入库校验
│   │
│   ├── skills/             # 内置 skill 加载器
│   │   ├── loader.py       # 扫描 SKILL.md、三级披露注入
│   │   └── __init__.py
│   └── rules/              # 规则文件加载器
│       ├── loader.py       # 扫描 rules/*.md、frontmatter 触发器
│       └── __init__.py
│
├── rules/                  # 项目级规则文件
│   └── communication-style.md
│
├── tests/                  # 测试（纯逻辑，不碰真实 LLM/embedding）
│   ├── test_config.py
│   ├── test_blocks.py
│   ├── test_chunking.py
│   ├── test_memory_history.py
│   ├── test_skills_rules.py
│   ├── test_longterm.py
│   ├── test_embed.py
│   ├── test_events.py
│   ├── test_agent_loop.py
│   ├── test_history_tool_calls.py
│   └── test_llm.py
│
└── data/                   # 运行时数据（.gitignore，不提交）
    ├── memory/             # human.md / persona.md / lessons.md（独立 git 仓库）
    ├── history/            # 会话 JSONL
    ├── logs/               # alfred.log（5MB 轮转 ×3）
    └── vectordb/           # LanceDB + mem0 Qdrant 本地数据
```

## 技术栈

| 层级 | 选型 | 说明 |
|---|---|---|
| Agent 框架 | Pydantic AI | 模型无关、统一消息模型、支持跨模型续跑 |
| 长期记忆 | mem0ai + 本地 Qdrant | 开源版，异步写入，失败降级；通过 MemoryClient 协议可插拔 |
| 核心记忆块 | 自研 `MemoryBlocks` + GitPython | human/persona/lessons 常驻 prompt，git 版本化 |
| 情景记忆 | 四元组 + LanceDB | 成功经验的结构化案例库 |
| 向量存储 | LanceDB | 嵌入式，notes/frameworks/episodes 三张表 |
| Embedding | sentence-transformers / OpenAI 兼容 API | 本地或云端均可，由 `models.embed.provider` 控制 |
| CLI | Typer + Rich + prompt-toolkit | 命令与交互式对话 |
| 配置 | PyYAML + pydantic + python-dotenv | `config.yaml` + `.env` |
| 测试 | pytest | 纯逻辑测试 |

## 配置入口（config.yaml）

`config.yaml` 是唯一的模型/路径配置入口，代码里不得硬编码模型名或 API 地址。

关键字段：
- `providers.<name>`：声明式 provider，含 `type`（`openai_compat` / `anthropic` / `gemini`）、`base_url`、`env_key`、可用 `models`
- `models.chat`：闲聊模型，对话中可用 `/model provider:model` 自由切换
- `models.memory_write`：记忆写入/复盘模型，固定强模型，质量敏感
- `models.embed`：embedding 模型配置。`provider: local`（本地 sentence-transformers）或 `provider: openai_compat`（云端 embedding API）。支持 `hf_endpoint` 镜像地址、`local_dir` 本地目录、`base_url`/`env_key`/`api_key` API 鉴权。模型一旦选定不要换，否则 notes/frameworks/episodes 向量库需要全量重建。
- `memory.*`：记忆块字符上限、召回硬预算、近因半衰期、`provider`（记忆客户端选择）、`default_user_id`（多 agent 共享时的租户隔离）
- `paths.*`：history/vectordb/skills/rules 目录

模型引用格式统一为 `provider:model`，由 `config.resolve()` 解析。

## 代码组织与模块职责

### CLI（`alfred/cli.py`）
- 所有用户命令入口：`chat`、`ingest`、`feed`、`frameworks`、`consolidate`、`memory`、`skills`、`models`
- `chat` 内支持斜杠命令：`/exit`、`/new`、`/model`、`/remember`、`/memory`、`/why`、`/sessions`、`/load`、`/delete`、`/lessons`、`/status`、`/help`
- `chat` 启动选项：`--session/-s` 恢复会话、`--debug` 启用调试日志输出到控制台
- `chat` 交互使用 `prompt_toolkit.PromptSession`：支持行编辑（光标移动、删除、历史）、长输入；发送后显示 `助手正在思考...` spinner，收到首个事件后切换为 `助手：` 前缀；工具调用单独成行显示
- 所有 Rich Console 输出集中在主线程渲染，避免与后台 agent 线程竞争；用户确认回调 `_confirm` 用原生 `print/input` 实现以降低线程安全风险
- chat 日志通过 `_setup_chat_logger` 写入 `data/logs/alfred.log`（RotatingFileHandler，5MB×3），`--debug` 时同时输出到控制台；记录会话开始/结束、每轮输入/回复长度、工具调用、异常堆栈
- `/status`：在对话内检查当前 chat 模型与 embedding 的连接状态
- `/lessons`：查看 RefleXion 教训库，支持按类别过滤（如 `/lessons code-debug`）

### Agent 内核（`alfred/agent.py`）
- `build_agent()`：组装 Pydantic AI Agent，绑定模型、四层 system prompt、恒定工具集
- `chat_turn_stream()`：单轮对话循环入口，基于 `EventBus` 以流式事件（`TurnStart`、`AssistantChunk`、`ToolCallStart`、`ToolCallEnd`、`ToolDenied`、`TurnEnd`、`TurnError`、`ContextCompacted`）返回运行过程；循环外通过订阅 `EventBus` 观察事件，不侵入核心逻辑
- `_wrap_tool()`：为所有工具统一包装确认流程与生命周期事件（`ToolCallStart`/`ToolCallEnd`/`ToolDenied`），单轮工具调用硬上限 20 次
- `chat_turn()`：`chat_turn_stream()` 的同步兼容包装，drains 事件流后返回最终文本
- `AlfredDeps`：运行时依赖对象（config、blocks、confirm 回调、本轮召回记录、session_id、bus、tool_call_count）

**System prompt 四层顺序（KV-cache 纪律，不可随意调整）：**
1. 静态层：`INSTRUCTIONS`（人格/行为准则/工具准则）
2. 半静态层：`persona` 块 + `human` 块（`inject_persona`、`inject_human`）
3. 半动态层：`lessons` 块（`inject_lessons`）—— RefleXion 教训，随对话积累自动更新
4. 静态缓存层：`skill_index` + `lessons_text`（build_agent 时一次性预加载，避免每轮 I/O）
5. 动态层：常驻规则 + 可召回规则索引 + 当前日期（`inject_rules`、`inject_date`）

**恒定工具集（`agent.py` 中注册，共 8 个）：**
- `memory_search`：长期记忆召回（混合相关性 + 近因排序）
- `memory_update_block`：更新 human/persona 块（human 和 persona 修改均需用户确认）
- `notes_search`：笔记 RAG
- `episodes_search`：情景记忆检索（借鉴过去的成功经验）
- `file_read`：读取技能/规则/任意文本文件
- `shell`：执行 shell 命令（需用户确认）
- `run_python`：执行 Python 代码（需用户确认）
- `code_patch`：自举进化工具，精确替换源代码中的一段文本（需用户确认，三重门禁，单轮最多一次）

### 自举进化（`alfred/codewriting.py`）

- `code_patch(relative_path, old_string, new_string)`：agent 修改自身源代码的工具
- **三重门禁**：路径门禁（只允许 `alfred/` 和 `config.yaml`）→ 语法门禁（`py_compile`）→ 测试门禁（`pytest`），任一失败自动回滚
- **理论依据**：CodeAct (Wang et al., ICML 2024, arXiv:2402.01030) 的代码即动作空间 + SWE-bench (Jimenez et al., 2023) 的 patch→测试验证范式
- **约束**：人类是进化方向决策者，agent 不能自行决定"该改什么"；单轮最多一次调用；`tests/` 不可写（防止 agent 自证清白）
- **用户确认**：`_confirm_prompt` 展示旧代码和新代码预览，用户拒绝则写入被拒绝

### 记忆层（`alfred/memory/`）

**协议层：**
- `protocols.py`：定义 `MemoryClient`（`add`/`search`/`list_all`/`delete`）和 `EmbeddingClient`（`encode`/`dims`）协议，用于记忆后端可插拔。新增云端或其他 provider 只需实现协议。
- `local.py`：`LocalMemoryClient`，基于 mem0 + 本地 Qdrant 实现 `MemoryClient` 协议。

**记忆块：**
- `blocks.py`：`human`/`persona` 两个常驻记忆块，字符上限 `memory.block_char_limit`，每次修改自动 git commit；git commit 失败时记录 warning（不再静默吞掉）
- `lessons.py`：`LessonsBlock`，RefleXion 教训记忆块（追加型，上限 4000 字符，超限自动压缩保留最近 20 条），与 human/persona 共用同一个 memory git 仓库

**长期记忆：**
- `longterm.py`：记忆客户端工厂（用户级懒加载单例），按 `config.memory.provider` 分发到不同 MemoryClient 实现；`add_async`/`search`/`list_all`/`delete` 均支持 `user_id` 参数，用于多 agent 共享时的租户隔离
- 琐碎消息过滤：用户/助手消息 strip 后 < 20 字符或匹配 `^是$`/`^嗯`/`^ok` 等模式则跳过，不送入 mem0
- 初始化失败降级：client 创建失败时标记 `_init_failed`，后续操作空执行，不阻塞对话

**召回与情景：**
- `recall.py`：混合召回入口，按 `recall_budget` 硬预算截断，融合相关性（0.7）+ 近因（0.3）排序
- `episodic.py`：情景记忆四元组（场景/思路/行动/结果），存 LanceDB `episodes` 表，`search_episodes` 支持语义检索

**整理：**
- `consolidate.py`：sleep-time 整理，产出 memory_entries / human_block_update / rule_suggestions / stale_memories / lessons 五类草稿；`apply_drafts` 逐项确认后入库，`apply_unattended` 无人值守模式自动写入 lessons、其余草稿暂存到 `data/history/consolidate_pending.jsonl` 待用户审查
- `consolidate_state.py`：append-only JSONL 追踪对话轮数与最近复盘时间；`should_auto_consolidate()` 在 `chat` 退出时判断是否自动触发无人值守 consolidate（阈值：≥3 轮且距上次复盘 >24 小时），后台线程执行不阻塞退出
- `audit.py`：记忆审计视图（`alfred audit` / `/audit`），诊断记忆库健康度、工具调用成功率、冷笔记、死规则、过期目标
- `monitor.py`：自监控度量（工具调用/记忆召回/skill 使用按天统计），供未来可视化/告警接入

**自动复盘流程**：
1. 每轮对话结束追加一条 turn 记录到 `consolidate_state.jsonl`
2. `/exit` 时检查是否满足自动条件 → 是则启动后台线程跑 `apply_unattended`
3. 用户下次进入 chat 运行 `/consolidate-review` 查看暂存草稿，再手动 `alfred consolidate` 逐项确认

### 知识层（`alfred/knowledge/`）
- `chunking.py`：按 Markdown 标题层级切分，保留标题路径前缀，解析 frontmatter
- `embed.py`：embedding 模型封装，支持本地 sentence-transformers 和云端 OpenAI 兼容 API；query 带 instruction 前缀
- `store.py`：LanceDB 表操作（`notes`、`frameworks`、`episodes`）
- `ingest.py`：增量索引 Markdown 目录（文件 hash 判断变更）
- `feed.py`：喂书管线，分段提炼框架卡片，四要素校验后入库

### 配置与模型（`alfred/config.py`、`alfred/llm.py`）
- `config.py`：Pydantic 模型 + YAML 加载 + `.env` 加载 + 路径解析；`MemoryConfig` 包含 `provider`（Literal["local"]）和 `default_user_id`（str）
- `llm.py`：把 `provider:model` 映射为 `AnthropicModel` / `OpenAIChatModel` / `GeminiModel`

## 开发约定

1. **配置驱动**：`config.yaml` 是唯一模型配置入口，代码里不得硬编码模型名或 API 地址。
2. **文档同步**：每次修改功能、命令、交互逻辑或使用方法时，必须同步更新 `README.md`（用户视角）和 `AGENTS.md`（开发视角）。禁止"代码改了文档没动"。
3. **记忆层与知识层严格分离**：
   - `alfred/memory/`：agent 对用户的认知
   - `alfred/knowledge/`：用户笔记/喂养材料
4. **记忆写入路径固定强模型**：长期记忆抽取、consolidate 复盘必须走 `models.memory_write`；闲聊路径才允许切换模型。
5. **用户确认强制化**：persona/human 修改、shell 执行、run_python 执行必须走 `deps.confirm` 用户确认，在代码层强制，不靠 prompt 约束。
6. **System prompt 顺序不可随意调整**：静态 instructions → memory blocks → lessons → 动态层（规则/skills/日期）。
7. **会话历史 append-only**：`history.py` 以 JSONL append-only 写入；压缩时整体重写并作废 `llm_state`。
8. **上下文压缩纪律**：丢内容留指针，用户偏好/进行中任务最高保留优先级，工具输出不二次截断（压缩前已裁剪）。
9. **记忆块版本化**：`data/memory/` 是独立 git 仓库，`human`/`persona`/`lessons` 每次修改自动 commit。
10. **测试不依赖真实 LLM**：所有测试不得调用真实模型 API 或下载 embedding，只测纯逻辑。
11. **路径相对项目根解析**：配置中的相对路径基于 `PROJECT_ROOT` 解析，`~` 自动展开。
12. **提交必须为单一逻辑单元**：每次 commit 只能包含一个功能/修复/文档主题，禁止混提；同文件多主题改动应使用 `git add -p` 拆分，提交信息遵循 Conventional Commits。
13. **记忆客户端可插拔**：新增记忆后端只需实现 `MemoryClient` 协议并注册到 longterm 工厂；消费者通过 `user_id` 实现租户隔离。
14. **工具调用频率限制**：单轮对话工具调用硬上限 20 次，防止 agent 无限循环。
15. **重要功能先做理论溯源**：实现任何重要功能前，必须先查阅相关学术论文（arXiv）和成熟 OSS 实现，产出理论溯源表（论文 → 核心机制 → 与方案的关系），再给出设计。禁止自创理论——学术界已有方案直接借鉴。无理论依据的设计必须诚实指出并修正。
16. **自举进化需人类确认**：agent 可以修改自身代码（`code_patch`），但不自行决定"该改什么"。进化方向必须由用户明确指示或 consolidate 发现的明确问题触发，每次修改需用户确认。

## 测试策略

```bash
python -m pytest tests/ -q
```

测试文件与覆盖范围：
- `test_config.py`：配置模型、provider 解析、env_key 读取
- `test_blocks.py`：memory blocks 读写、字符上限、git 版本化、回滚
- `test_chunking.py`：Markdown 切分、frontmatter 解析、超长段落二次切分
- `test_memory_history.py`：召回预算/近因排序、会话历史持久化与重写
- `test_skills_rules.py`：skills/rules 扫描、三级披露、frontmatter 触发器
- `test_longterm.py`：mem0 embedder 配置（local/openai_compat）、MemoryClient 协议
- `test_embed.py`：embedding 配置与编码
- `test_events.py`：事件总线与事件类型
- `test_agent_loop.py`：agent 工具调用循环、消息保序、事件流
- `test_history_tool_calls.py`：工具调用记录持久化
- `test_llm.py`：LLM 连接测试与错误映射

**约束**：测试不依赖真实 LLM 调用、不下载 embedding 模型、不访问外部 API。

## 安全与隐私

- **数据本地优先**：所有记忆、笔记索引、会话历史默认存储在 `data/` 目录（已被 `.gitignore` 排除）。
- **API key 不提交**：`.env` 被 `.gitignore` 忽略，API key 通过 `env_key` 在 `config.yaml` 中声明引用。
- **危险操作需确认**：shell / run_python / persona / human 修改必须用户确认，确认函数由 CLI 注入。
- **Telemetry 关闭**：mem0 中设置 `MEM0_TELEMETRY=false`，防止私人数据上报。
- **文件读取范围**：`file_read` 工具目前只检查文件存在性，不限制路径范围；读取用户指定文件时按操作系统权限执行。
- **命令执行超时**：shell / run_python 默认 60 秒超时，输出截断至 5000 字符。
- **工具调用上限**：单轮对话硬上限 20 次，防止 agent 失控。

## 扩展机制

### Skills
- 格式：`~/.agents/skills/<name>/SKILL.md`，YAML frontmatter + Markdown 正文
- 必填字段：`name`、`description`
- 可选字段（零侵入，仿 Kimi Code skill 机制）：
  - `when-to-use`：触发场景的精确描述，注入索引时单独标注为"触发：…"，辅助 LLM 判断是否调用
  - `disable-model-invocation: true`：标记为"仅用户主动触发"，注入索引时单独归类并附显式禁令，LLM 不会自动调用
- 启动时将每个 skill 的 name + description + when-to-use 注入系统 prompt，LLM 自行判断任务是否匹配
- 推荐正文分节：Procedure / Specifications / Advice / Forbidden Actions / Required from User
- 扫描目录由 `config.yaml` 的 `paths.skills_dirs` 控制，默认 `~/.agents/skills`
- 项目级 `skills/` 目录已废弃：skill 即用户资产，统一放在 `~/.agents/skills/` 跨项目复用

### Rules
- 格式：`rules/*.md`，含 YAML frontmatter
- 触发器：
  - `alwaysApply: true`：常驻注入 system prompt
  - `description`：进入可召回规则索引，由 agent 用 `file_read` 激活
  - `globs`：按文件 glob 匹配（语义预留，当前未实现自动匹配）
- 扫描目录由 `config.yaml` 的 `paths.rules_dirs` 控制

## 运行时数据布局

```
data/
├── memory/                 # 独立 git 仓库
│   ├── .git/
│   ├── human.md
│   ├── persona.md
│   └── lessons.md          # RefleXion 教训库
├── history/
│   └── <session_id>.jsonl  # 归一化消息 + llm_state 记录
├── logs/
│   └── alfred.log          # 对话日志（5MB 轮转 ×3）
└── vectordb/
    ├── lancedb/            # LanceDB 数据
    └── qdrant_mem0/        # mem0 本地 Qdrant
```

## 常见坑点

- **换 embedding 模型必须重建索引**：`models.embed.name` 一旦确定不要轻易更换，否则 notes/frameworks/episodes 向量库需要全量重建。
- **mem0 初始化失败会静默降级**：`longterm.py` 初始化失败会标记 `_init_failed`，后续记忆写入/召回都为空操作，不会阻塞对话。常见原因是 Qdrant 本地存储残留 `.lock` 文件（异常退出导致），`get_client` 已支持自动清理锁文件并重试一次。
- **mem0 Qdrant 锁文件残留**：如果 `alfred memory list` 长期为空且对话看似有记忆（实际是会话历史），检查 `data/vectordb/qdrant_mem0/.lock` 是否存在。`get_client` 会自动清理，若仍失败可手动删除该文件后重启。
- **persona 和 human 修改都需要用户确认**：agent 调用 `memory_update_block(name="persona")` 或 `memory_update_block(name="human")` 时，若 `deps.confirm` 返回 False 则写入被拒绝。
- **压缩会作废 llm_state**：`compaction.py` 蒸馏旧消息后调用 `session.rewrite()`，跨模型精确续跑回退为"摘要 + 近期消息"的种子上下文。压缩前已对长消息做过裁剪，蒸馏时不再二次截断。
- **聊天内切换模型会重建 agent**：`/model provider:model` 会调用 `build_agent(config, arg)`，历史消息以归一化 transcript 做种子上下文。
- **`alfred` 命令报 `ModuleNotFoundError`**：说明当前 Python 环境没有安装 alfred 包，或安装时指向了其他目录。先 `pip uninstall alfred`，再到项目根目录执行 `pip install -e ".[dev]"`。
- **Rich Console 与后台线程混用会导致输出错乱或卡死**：`chat` 的渲染必须在主线程完成，事件监听只做数据传递，不直接操作 Console。
- **mem0 后台写入可能污染终端**：`longterm.add_async` 的后台线程内用 `contextlib.redirect_stdout/stderr` 屏蔽所有输出，防止 mem0 或依赖库意外打印内容干扰 prompt_toolkit 输入。
- **Windows asyncio Ctrl+C 报错**：`cli.py` 中已设置 `WindowsSelectorEventLoopPolicy()`，避免 prompt-toolkit 在 Ctrl+C 时报 "Cancelling an overlapped future failed"。

## 部署与分发

当前为纯 Python CLI 包，通过 `pyproject.toml` 的 `[project.scripts]` 注册 `alfred` 命令：

```toml
[project.scripts]
alfred = "alfred.cli:app"
```

分发方式：
- 开发（conda）：`conda env create -f environment.yml && conda activate alfred`
- 开发（pip）：本地 `pip install -e ".[dev]"`
- 无容器/云部署脚本，当前阶段以本地个人使用为主

## 提交规范

项目约定使用 Conventional Commits，**每次提交必须是单一逻辑单元**，禁止把无关改动混在同一个 commit 中。

具体要求：

1. **单一逻辑单元**：一个 commit 只包含一个功能、修复、文档或测试主题。例如 `feat` 和 `fix` 不能混在同一个 commit 里。
2. **同文件交织也要拆分**：如果多个逻辑改动落在同一个文件里，应使用 `git add -p` 分多次提交，而不是合并成一个。
3. **提交信息格式**：`<type>: <简短描述>`，type 从下面选择：
   - `feat`: 新功能
   - `fix`: 修复
   - `docs`: 文档
   - `test`: 测试
   - `chore`: 杂项
   - `refactor`: 重构
4. **禁止的提交**：`git commit -a` 一把提交多个无关文件、包含未完成的临时改动、或把 bugfix/feature/docs 混在一起。

示例：
```bash
# 正确：分开提交
git add -p alfred/config.py alfred/knowledge/embed.py
git commit -m "fix: support hf_endpoint to bypass huggingface timeout"
git add -p alfred/config.py alfred/knowledge/embed.py
git commit -m "feat: support openai_compat embedding provider"

# 错误：混在一起
git commit -a -m "update embed"
```

## 参考文档

- `README.md`：用户视角快速开始
- `docs/plans/2026-08-implementation-plan.md`：完整架构实施计划与设计依据
- `docs/research/2026-08-personal-butler-agent-tech-selection.md`：技术选型调研报告
- `config.yaml`：配置示例与注释
- `.env.example`：API key 环境变量模板