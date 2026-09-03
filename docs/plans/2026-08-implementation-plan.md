# 私人管家 Agent — 完整架构实施计划（v2，已吸收主流 Agent 设计共识）

## 已确认的决策
- 路线：纯 Python 自建内核，借鉴 Letta 的记忆设计（human/persona memory blocks + sleep-time 整理）
- 形态：CLI 优先，后续可扩 Web；云端 API 为主
- 环境：Python 3.13.2，`python3 -m venv .venv` + pip

## 设计依据（每个子系统抄谁的、怎么抄）

方案不是拍脑袋，每个子系统都有主流生产级 agent 的验证过设计做依据：

| 子系统 | 借鉴来源 | 具体借鉴的设计 |
|---|---|---|
| System prompt 组装 | Manus（KV-cache 工程）+ Anthropic context engineering | 分层：静态（人格/准则）→ 半静态（memory blocks）→ 动态（日期、召回内容）；时间戳放消息层不放 prompt 头；prompt 前缀稳定、上下文 append-only |
| 工具系统 | Manus + Anthropic《Writing tools for agents》 | 工具集会话内恒定（mask 不删）；按工作流合并不按 API 平铺；命名空间前缀（`memory_*`/`notes_*`）；返回高信号内容；错误信息可操作化；预留 `run_python` 代码执行工具作 CodeAct 逃生舱 |
| 上下文压缩 | Manus + Claude Code + Chroma context-rot 实证 | 丢内容留指针（`[已裁剪，见 path#L20]`）；用户偏好类陈述最高保留优先级；召回硬预算（≤10 条，相关性+重要性+近因排序截断）——"塞得下"≠"该塞" |
| 记忆分层 | Letta + LangMem 记忆类型学 + ChatGPT Memory | 三层混合召回：profile 型 blocks 常驻 + collection 型（mem0）按需工具召回 + background 整理晋升；增加**情景记忆层**（成功案例四元组）；记忆对用户可见可编辑（`memory list/edit`），回答时可说明用了哪些记忆 |
| mem0 缺口补足 | ChatGPT 新版 memory 设计 | mem0 v3 开源版 ADD-only（只增不改），自建 consolidation 步骤做记忆整合/去矛盾/更新 |
| Skills | Anthropic Agent Skills（progressive disclosure 三级）+ Devin Playbook 分节 | metadata（~100 tokens）→ SKILL.md 正文 → 资源文件；SKILL.md 采用 Playbook 分节：Procedure / Specifications / Advice / Forbidden Actions / Required from User |
| 规则文件 | Cursor rules + AGENTS.md | frontmatter 四触发器语义：alwaysApply / 按 description 智能召回 / globs 匹配 / 手动触发 |
| 自我成长 | Devin Knowledge Suggestions + Anthropic 官方建议 | 收敛形态：**复盘 → 草稿 → 确认入库 → git 版本化**。产出三类候选：记忆条目 / 规则修订 / skill 草稿；不做全自动无确认改写 |
| 子智能体（平台化预留） | Cognition《Don't Build Multi-Agents》+ Anthropic 多智能体研究系统 | 一期只做 agents-as-tools 只读调查形态：子 agent 回传蒸馏摘要、产出落盘；主 agent 单线程持有全量上下文；handoff 推迟 |
| 固定流程 | Anthropic《Building effective agents》workflow/agent 分治 | 提醒、晨报等固定流程走 cron + prompt chain（workflow），不进 agent loop |
| 确定性强制规则 | Claude Code hooks 思想 | "修改 persona 必须用户确认"等放代码层强制（必然执行），不靠 prompt 约束（概率性遵守） |

## 目标架构

```
┌──────────────────────────────────────────────┐
│ CLI（Typer + Rich）：chat / ingest / feed /    │
│ consolidate / memory / skills / models        │
├──────────────────────────────────────────────┤
│ Agent 内核（Pydantic AI）                      │
│  System prompt 分层（KV-cache 友好顺序）：       │
│   ① 静态：persona + 行为准则 + 工具准则          │
│   ② 半静态：human block + 常驻规则文件           │
│   ③ 动态：skills 索引 + 本轮召回                 │
│  工具（恒定集合）：notes_search / memory_search /│
│   memory_update_block / file_read / shell /    │
│   run_python                                  │
│  会话历史：JSONL 归一化持久化（跨模型可续跑）     │
│  Compaction：丢内容留指针 + 偏好优先保留          │
├──────────────────────────────────────────────┤
│ 记忆层（agent 对用户的认知）——三层混合召回        │
│  ├─ Profile：data/memory/human.md persona.md   │
│  │  （常驻 prompt，git 版本化，字符上限）         │
│  ├─ Collection：mem0（异步写入，强模型抽取，      │
│  │   自建 consolidation 补 ADD-only 缺口）       │
│  ├─ 情景记忆：成功案例四元组库（LanceDB）         │
│  └─ Sleep-time：consolidate → 三类草稿          │
│     （记忆/规则/skill）→ 确认 → 晋升入库         │
├──────────────────────────────────────────────┤
│ 知识层（笔记与喂养材料）——与记忆严格分层          │
│  ├─ RAG：LanceDB + Qwen3-Embedding-0.6B 本地   │
│  │   + Markdown 标题层级切分 + frontmatter     │
│  └─ 思维框架库：feed 管线提炼的框架卡片          │
├──────────────────────────────────────────────┤
│ 模型入口：config.yaml 声明式 provider 配置       │
│ 记忆写入路径固定强模型，闲聊路径自由切             │
└──────────────────────────────────────────────┘
```

## 项目结构

```
agentmyself/
├── pyproject.toml
├── .env.example
├── config.yaml                 # providers / models.chat / models.memory_write / models.embed
├── butler/
│   ├── cli.py                  # Typer 命令入口
│   ├── config.py               # pydantic-settings
│   ├── llm.py                  # config → pydantic-ai model 实例
│   ├── agent.py                # prompt 分层组装 + 恒定工具集 + 对话循环
│   ├── history.py              # JSONL 归一化持久化
│   ├── compaction.py           # 上下文压缩：留指针、偏好优先、预算截断
│   ├── memory/
│   │   ├── blocks.py           # human/persona 读写 + git 版本化 + 字符上限
│   │   ├── longterm.py         # mem0 封装 + 自建 consolidation
│   │   ├── episodic.py         # 成功案例四元组（情景记忆层）
│   │   ├── recall.py           # 混合召回：预算/排序（相关性+重要性+近因）/晋升
│   │   └── consolidate.py      # sleep-time：复盘→三类草稿→确认→入库
│   ├── knowledge/
│   │   ├── chunking.py         # Markdown 标题切分 + frontmatter
│   │   ├── embed.py            # Qwen3-Embedding-0.6B（sentence-transformers）
│   │   ├── store.py            # LanceDB 表：notes / frameworks / episodes
│   │   ├── ingest.py           # 增量索引（文件 hash）
│   │   └── feed.py             # 喂书：分段通读→框架卡片→校验→入库
│   ├── skills/
│   │   └── loader.py           # 三级披露：metadata 注入→正文按需读→资源文件
│   └── rules/
│       └── loader.py           # 规则文件：Cursor 式 frontmatter 四触发器
├── skills/
│   ├── software-dev-workflow/SKILL.md   # Playbook 分节结构
│   └── framework-distiller/SKILL.md
├── rules/                      # 示例规则文件
├── data/                       # 运行时数据（.gitignore）
│   ├── memory/                 # core blocks（独立 git 仓库）
│   ├── history/
│   └── vectordb/
└── tests/
```

## 关键依赖
`pydantic-ai`、`mem0ai`、`lancedb`、`sentence-transformers`、`typer` + `rich` + `prompt-toolkit`、`pydantic-settings`、`pyyaml`、`gitpython`、`pytest`

## 实施步骤

### Step 1 — 脚手架与配置层
- venv + pyproject.toml；`pip install -e .` 提供 `butler` 命令
- `config.yaml`：providers（base_url/env_key/models）、`models.chat` / `models.memory_write`（固定强模型）/ `models.embed`（本地 Qwen3）、记忆召回预算等阈值
- 验证：`butler models` 列出并校验配置；pytest 空跑通过

### Step 2 — 核心对话 loop（含上下文工程纪律）
- `llm.py` + `agent.py`：prompt 三层组装（静态→半静态→动态），时间戳等易变信息放消息层；工具集恒定
- `history.py`：JSONL 归一化（剥离 provider 私有格式）
- `compaction.py`：超长时蒸馏压缩——用户偏好/进行中任务最高保留优先级，工具输出裁剪为指针引用
- `cli.py chat`：流式输出；斜杠命令 `/exit /new /model /remember /memory`
- 验证：真实 API key 多轮对话；`/model` 切换后续聊正常；人为构造长会话触发 compaction 且不丢用户偏好

### Step 3 — 记忆系统
- `blocks.py`：模板 + 字符上限 + 每次修改 git commit；`memory_update_block` 工具（persona 修改走用户确认——代码层强制）
- `longterm.py`：mem0 异步写入（对话轮结束后台线程）；`memory_search` 工具按需召回（硬预算 ≤10 条，相关性+重要性+近因排序）
- `episodic.py`：任务成功后存四元组（场景/思路/行动/结果），供 few-shot 召回
- `recall.py`：三层混合召回 + background 晋升（高频高重要条目升入 blocks）
- `butler memory list/edit`：记忆可见可编辑；回答后可用 `/why` 查看本轮用了哪些记忆
- 验证：告知个人信息 → 新会话能 recall；human.md 自动更新且 git log 可查；mem0 旧事实能被 consolidation 更新而非矛盾堆积

### Step 4 — 知识库 RAG
- `chunking.py` / `embed.py` / `store.py` / `ingest.py`：标题层级切分（保留标题路径前缀）、frontmatter 元数据、增量索引
- `notes_search` 工具：带过滤、返回引用路径（呼应"留指针"压缩）
- 验证：索引真实笔记，事实性问题能答出并引用正确文件

### Step 5 — Skills 与规则系统
- `skills/loader.py`：三级披露（metadata 常驻 ~100 tokens/skill → agent 用 file_read 激活正文 → 资源文件按需）
- `rules/loader.py`：Cursor 式 frontmatter（alwaysApply / description 召回 / globs / 手动）
- 内置：`software-dev-workflow`（Playbook 分节：Procedure/Specifications/Advice/Forbidden/Required from User）、`framework-distiller`
- 验证：提"开发 App"主动激活 dev-workflow；规则按触发器正确加载

### Step 6 — 自我成长循环
- `/remember` 显式教学 → human block
- `butler consolidate`：强模型复盘近期会话 → 产出三类草稿（mem0 记忆条目 / 规则修订 / skill 草稿）→ 用户确认 → 入库 + git commit（可回滚）
- `butler feed <文件>`：分段通读 → 按 framework-distiller 提炼框架卡片（名称/核心观点/适用场景/来源，缺项拒收——Voyager 式入库校验）→ frameworks 表；重要框架仅"建议"回写 persona，人工确认
- 验证：feed 短文出结构良好卡片；consolidate 产出草稿、确认后落库、可 git revert

### Step 7 — 测试与文档
- pytest：blocks 上限与版本化、chunking、skills/rules loader、recall 预算与排序、history 归一化、compaction 保偏好
- README.md（架构图 + 设计依据表 + 命令一览）、AGENTS.md
- 最终 e2e：对话 → 记忆 → RAG → skill 激活 → consolidate 全链路

## 需要用户配合
1. 至少一个云端 LLM API key（写入 `.env`）；记忆写入路径建议配强模型
2. embedding 模型首次下载约 600MB（HuggingFace；网络受限可换 Ollama/在线 embedding）
3. 笔记目录路径（用于 `butler ingest`）

## 明确不做（本期范围外）
- Web 界面、IM 渠道
- Graphiti 图谱记忆、MCP 对接（skills + 工具先覆盖）
- handoff 式多智能体编排（一期仅 agents-as-tools 只读调查，且本期只留接口不实现）
- 全自动无确认的自我改写（一切沉淀走草稿+确认）

## 已确认问题（暂缓，待后续优化）

### P1 — 更换 embedding 模型会导致向量库静默失效，且无任何校验
**记录日期**：2026-08-30

**现象**：换 embedding 模型后向量库不可用，但程序不崩溃、无告警。分两种情况：

| 情况 | 表现 |
|---|---|
| 新模型维度**不同**（如 1024→384） | qdrant 插入时报 `Expected vector of size 1024, got 384`，但该异常在 `longterm.py:160` 被 catch 后只打日志，`audit.py:158` 仅报「长期记忆不可用」。**长期记忆悄悄停写**。 |
| 新模型维度**相同**（如 1024→另一 1024 模型） | 无任何报错，新向量照常入库。但新旧向量处于**不同语义空间**，跨边界 cosine 计算为噪声，召回返回不相关记忆。这是最危险的情况，`audit` 抓不到。 |

**根因**
- 代码中**不存在维度校验**：`knowledge/embed.py:154` 暴露了 `dim()`，但没有任何调用方拿它比对配置或既有库 schema。
- `memory/local.py:73` 硬编码回退：`embed_cfg.dims or 1024` —— 不填 `dims` 时按 1024 建集合，与实际模型输出维度无关。
- 两个向量库需分别处理：qdrant（mem0 长期记忆，集合维度固定）与 lance（episodes/notes，表 schema 固定）。

**注意**：`embed.py:48` 的 `normalize_embeddings=True` + qdrant Cosine 只保证量纲干净，**不等于**两个模型的空间可比较。

**当前人工补救流程**（已验证可行）
```bash
# 1. config.yaml 显式填 dims = 新模型真实维度（不填会回退 1024，重新落进情况 A）
# 2. 删向量索引（ingest_state.json 必须一起删，否则空库认为"已索引过"什么都不做）
rm -rf data/vectordb/qdrant_mem0 \
       data/vectordb/episodes.lance \
       data/vectordb/notes.lance \
       data/vectordb/ingest_state.json
# 3. 重建：mem0 集合按新 dims 自动创建
alfred ingest <笔记目录>
```

**数据影响**
- `data/memory/{human,persona,lessons}.md` 为纯文本，与 embedding 模型无关，**不受影响**。
- qdrant 中 mem0 抽取的记忆会丢失，但原始对话仍在 `data/history/`，跑 `/consolidate` 可从 transcript 重新提炼，**可恢复而非不可逆**。

**优化方向（待定）**
- 启动时校验：实际模型维度 vs 既有集合/表 schema，不一致则立即报错提示重建，而非静默降级。
- 去掉 `local.py:73` 的 `or 1024` 回退，`dims` 缺失时从模型探测而非假设。
- 提供显式 `alfred rebuild-index` 命令，封装上述删除+重建流程。

