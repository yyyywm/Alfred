# 私人管家个人 AI Agent 技术选型调研报告

> 调研日期：2026-08-11
> 调研方法：基于官方文档、GitHub 仓库（README / 源码 / star 数实时查询）、官方 blog、论文一手来源；不依赖二手技术文章。未能核实的信息在文中明确标注。

---

## 一、背景摘要

目标是构建一个"私人管家"个人 AI Agent，核心需求可归纳为八个方面：

1. **知识库 / RAG**：索引个人笔记（Markdown / Obsidian 库）并基于笔记问答
2. **长期记忆**：跨会话持久化记住用户的个人信息、经历、偏好、思维方式
3. **人格化**：像朋友/秘书/管家，随时间加深对用户的了解
4. **模型无关**：可切换任何大模型厂商 API（OpenAI / Anthropic / Gemini / DeepSeek / 本地模型），切换不影响系统行为
5. **可扩展平台**：作为基座，后续对接定制化子智能体；支持 skills 机制扩展能力
6. **自我成长**：用书籍等内容"喂养"，吸收思维模型；能自主汲取新知识
7. **专业工作流**：内置/可扩展领域流程知识（如软件开发流程）
8. **决策辅助与深度对话**：人生/职业规划探讨

本报告按六个技术方向调研：**Agent 框架、记忆系统、模型无关抽象层、RAG 与向量存储、Skills 扩展机制、自我成长机制**，最后给出综合推荐架构。

---

## 二、Agent 框架

### 2.1 总览对比表

| 框架 | 语言 | GitHub Stars（2026-08 实测） | 模型无关性 | 内置长期记忆 | 扩展机制 | 活跃度 |
|---|---|---|---|---|---|---|
| LangGraph / LangChain v1 | Python / TS | langgraph ~39.5k；langchain ~144k | 强（`init_chat_model` + content blocks） | 半成品（Store + middleware 自组） | tools / MCP / middleware | 极高，当日有提交 |
| Letta（原 MemGPT） | Python（老 server）+ TS（新 harness） | letta ~24.2k；letta-code ~3.0k | 强（含 DeepSeek / Ollama / OpenRouter） | ★ 最强（记忆块 + MemFS + dreaming） | skills / mods / MCP / channels | 活跃 |
| CrewAI | Python | ~56.9k | 强（底层走 LiteLLM） | 较强（统一 Memory 类，LLM 辅助写入） | tools / MCP | 极高 |
| Microsoft Agent Framework（AutoGen 后继） | Python / .NET | ~12.7k（AutoGen 本体 ~60.4k 但已维护模式） | 强 | 可插拔（需自接存储） | MCP / A2A / OpenAPI | 活跃 |
| OpenAI Agents SDK | Python / TS | ~28.6k | 中（支持 OpenAI 兼容端点，但高级特性实质绑定 OpenAI） | 仅 session，无长期记忆 | tools / MCP / handoffs | 极高 |
| Claude Agent SDK（原 Claude Code SDK） | Python / TS | ~7.9k | 弱（Claude only） | 无 | 极强（MCP / hooks / subagents / skills） | 活跃 |
| Pydantic AI | Python | ~19.2k | ★ 最强（25+ provider，统一消息模型可跨模型续跑） | 无（需自搭） | tools / MCP / capabilities | 极高 |
| Mastra | TypeScript | ~27.1k | 强（169 provider / 5000+ 模型路由） | 较强（working memory + semantic recall + Observational Memory） | tools / MCP / workflows | 极高 |

### 2.2 各框架要点

**LangGraph / LangChain v1**
- v1.0 核心收敛为 `create_agent`（"Agent = Model + Harness"），引入 middleware 钩子机制（`before_model` / `wrap_model_call` / `after_agent`），内置 PII、摘要、Human-in-the-Loop 等中间件；旧 chains/retrievers 移入 `langchain-classic` 包。来源：https://docs.langchain.com/oss/python/releases/langchain-v1
- 记忆方面提供 checkpointer（线程级状态）+ Store（跨线程长期记忆）抽象，但"何时写记忆、写什么"的策略需自己实现。来源：同上
- 定位：生态最大的"保险选项"，什么都能拼出来，但长期记忆架构要 DIY。

**Letta**
- 重大变化：活跃开发已从老 server（letta-ai/letta，已标 legacy）迁移到 **Letta Code / Letta Harness**（TypeScript，`npm i -g @letta-ai/letta-code`），定位为"stateful、自我进化 agent"的开源 harness。自带：git 版本化记忆文件系统 **MemFS**、skills、schedules（定时任务）、sleep-time 子代理做记忆整理（"dreaming"）、mods（修改 harness 本身）、多渠道接入（Slack/Telegram/WhatsApp/Signal/Discord）。来源：https://docs.letta.com/llms.txt
- 记忆是其立身之本：记忆块由 agent 通过工具自编辑、可跨 agent 共享（这是 sleep-time 机制的基础）。来源：https://www.letta.com/blog/memory-blocks/
- 模型无关：Anthropic / OpenAI / Gemini / DeepSeek / xAI / Bedrock / Azure / OpenRouter / Ollama / LM Studio / llama.cpp / 任意 OpenAI 兼容端点。来源：https://docs.letta.com/llms.txt
- 官方自己的定位就是 "personal assistants / AI coworkers"，且可完全自托管无需 Letta 账号。代价：接受较重的整套 harness 范式；正处于 letta → letta-code 产品线迁移期（两套仓库、Python/TS 并存）。

**CrewAI**
- Flows（事件驱动工作流）+ Crews（多 agent 角色协作）。新版**统一 Memory 类**：LLM 辅助写入（推断 scope/类别/重要度）、层级 scope、复合评分召回（语义+时效+重要度）、记忆合并去重、后台异步写入；默认 LanceDB 本地存储。来源：https://docs.crewai.com/en/concepts/memory
- 心智模型是"多 agent 团队执行单次任务"，而非"单一长期身份的常驻管家"；记忆分析默认依赖云端 LLM（隐私注意）。

**AutoGen 系（重要变化）**
- microsoft/autogen 已进入 **Maintenance Mode**，官方 README 指引新用户转向 **Microsoft Agent Framework**（统一 Semantic Kernel 与 AutoGen，偏企业场景）。来源：https://github.com/microsoft/autogen
- 社区分叉 **AG2** v1.0 重写为协议驱动框架（含 KnowledgeStore / WorkingMemoryPolicy 等原语），但社区规模小（~4.9k）、稳定性待观察。来源：https://github.com/ag2ai/ag2
- 结论：不建议作为新项目基座。

**OpenAI Agents SDK**
- 原语少、上手快、内置 tracing / HITL / handoffs。可通过 `ModelProvider` 接任意 OpenAI 兼容端点，但很多高级特性（hosted tools、structured outputs 等）在非 OpenAI 后端退化，tracing 默认上传 OpenAI 需手动关闭。无长期记忆架构。来源：https://openai.github.io/openai-agents-python/models/
- 结论：实质深度绑定 OpenAI 生态，不符合"模型无关"硬指标。

**Claude Agent SDK**
- 本质是 Claude Code CLI 的编程封装，继承其全套工具、权限、hooks、subagents、skills。但锁定 Anthropic 模型、受 Anthropic 商业条款约束、SDK 层无长期记忆。来源：https://github.com/anthropics/claude-agent-sdk-python
- 结论：扩展体验一流，但模型锁定直接违反需求 4。

**Pydantic AI**
- 模型无关设计最彻底：模型就是字符串（`'anthropic:claude-sonnet-4-6'`），25+ provider，统一消息模型支持同一会话历史跨模型续跑（官方示例：GPT 的对话直接交给 Gemini 继续）。Model / Provider / Profile 三层抽象显式处理模型能力差异（如不同模型对 JSON schema 的限制）。来源：https://ai.pydantic.dev/ 、https://ai.pydantic.dev/message-history/ 、https://ai.pydantic.dev/models/overview/
- 无内置长期记忆，只有 message_history 参数 + 历史处理器 + Durable Execution（断点恢复适合常驻进程）。长期记忆需自搭或接 mem0。

**Mastra（TS 生态）**
- TS 里唯一"记忆 + 模型无关 + 工作流"三件齐全：working memory（结构化用户档案）+ semantic recall + **Observational Memory**（后台 agent 把旧历史压缩成观察日志，思路接近 Letta）。来源：https://mastra.ai/docs/memory/overview
- 模型路由基于 models.dev 数据，支持 fallback 链。来源：https://mastra.ai/models
- 缺点：绑定 Node/TS 栈，框架年轻、API 变动快。

### 2.3 框架方向结论

针对"模型无关 + 内置长期记忆 + 可扩展 skills"三个硬指标：

- **首选 Letta**：八个框架中唯一把"长期记忆与自我改进"作为产品核心，同时满足模型无关和 skills 扩展，且自带定时任务、IM 渠道、权限管理——正是私人管家形态。代价是接受其 harness 范式和迁移期不确定性。
- **轻量库路线**：Pydantic AI（Python，模型无关最干净）或 Mastra（TS，记忆最完整），长期记忆自搭（接 mem0 或自建 Letta 风格记忆块）。
- **生态保险**：LangGraph v1，但长期记忆需自己设计。
- **不建议**：AutoGen（维护模式）、Claude Agent SDK（锁 Anthropic）、OpenAI Agents SDK（实质锁 OpenAI 且无长期记忆）。

> 存疑标注：Claude Agent SDK 官方文档站调研时网络抓取失败，相关结论基于其 GitHub README；Letta 新老仓库的长期分工边界官方尚无明确时间表。

---

## 三、记忆系统

### 3.1 Letta 记忆架构（core / archival / recall）

经典 MemGPT 三层设计（来源：https://www.letta.com/blog/memory-blocks/ ，论文 https://arxiv.org/abs/2310.08560 ）：

- **Core memory**：常驻上下文窗口的 memory blocks，经典为 `human`（用户画像）和 `persona`（agent 自我设定）两块；每块有 label 和字符上限，由 agent 通过 memory tools **自我编辑**，可设只读。每个 block 独立持久化、可被多 agent 共享。
- **Archival memory**：上下文外存储，agent 主动写入，语义搜索召回。
- **Recall memory**：完整对话历史，可搜索。

**Sleep-time compute / dreaming**：agent 在空闲期用后台计算把"原始上下文"转化为"学习后的上下文"。工程实现为**双 agent 共享同一组 memory blocks**——主 agent 负责对话（快模型），sleep-time agent 独占记忆编辑工具、异步整理（可用更强模型）。论文报告同等精度下 test-time compute 降低约 5x。来源：https://www.letta.com/blog/sleep-time-compute 、https://arxiv.org/abs/2504.13171 。新版 harness 中演化为 "dreaming" + MemFS（git 版本化记忆文件系统）。来源：https://docs.letta.com/configuration/memory

**能否独立嵌入**：基本不能。Letta 官方明确立场是"记忆不是插件"（https://www.letta.com/blog/why-memory-isnt-a-plugin/ ），不提供 memory-only SDK。要用 Letta 的记忆需整体采用其 harness。

### 3.2 mem0

- GitHub：https://github.com/mem0ai/mem0 （~63.0k stars，Apache-2.0，极活跃）
- 原理：两阶段管线——写入时 LLM 提取持久事实 → 去重 → 嵌入 → 实体抽取；读取时多信号融合（语义向量 + BM25 + 实体匹配 + 时间推理）。来源：https://docs.mem0.ai/core-concepts/how-it-works
- **重要变化**：v3 起开源版改为单轮 ADD-only 提取（记忆只增不改），且 **graph memory 已从开源 SDK 移除**，变为托管 Platform 独有。来源：https://docs.mem0.ai/migration/oss-v2-to-v3
- 开源 vs 托管：开源版可作库嵌入（`pip install mem0ai`，Python + TS 双 SDK）或 Docker 自托管；默认 OpenAI + 本地 Qdrant + SQLite，全部可换（含本地 embedding）。Platform 独有 graph memory、Dream（后台记忆整合）等。来源：https://docs.mem0.ai/open-source/overview
- **独立嵌入能力最强**：22+ 官方框架集成（LangGraph / CrewAI / OpenAI Agents SDK / Mastra 等），另有 MCP server。来源：https://docs.mem0.ai/integrations/langgraph
- 注意：官方 benchmark 高分（LoCoMo 92.5）来自托管 Platform，开源版"方向性相似但数字不同"。

### 3.3 Zep / Graphiti

- **开源状态关键变化**：Zep 本体已闭源（Community Edition 停更，代码移入 `legacy/`），开源精力全部转向 **Graphiti**（https://github.com/getzep/graphiti ，~29.8k stars，Apache-2.0，活跃）。来源：https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/
- 原理：temporal knowledge graph——节点为实体、边为事实/关系；事实带**双时间有效性窗口**，新数据到来时旧事实被"失效标记"而非删除；支持 episode 溯源、增量构图、混合检索（语义 + BM25 + 图遍历）。论文：https://arxiv.org/abs/2501.13956
- 嵌入方式：Graphiti 为 Python 库（`pip install graphiti-core`），需自带 Neo4j / FalkorDB / Neptune；有官方 MCP server。Zep Cloud 为托管服务（Python / TS / Go SDK）。
- 模型无关性：默认 OpenAI，可换 Gemini / Anthropic / Groq，但官方警告依赖 Structured Output 能力，小模型可能输出格式错误——提取质量与所选 LLM 强相关。来源：https://github.com/getzep/graphiti

### 3.4 纯向量库方案作为"记忆"的局限

来源：https://www.letta.com/blog/rag-vs-agent-memory/

- RAG 是**单步反应式**的：用户说"今天是我生日"，向量库只会检索与 "birthday" 语义相似的旧消息；用户过去提过的"最喜欢的颜色"因语义不相关永远不会被召回——真正的个性化无法实现。
- 向量库存的是原始切片，**没有写入-更新-巩固的生命周期**：无去重、无矛盾处理、无时间有效性。
- 结论：RAG 适合知识库（需求 1），不能替代记忆系统（需求 2）。两者是互补的两层。

### 3.5 记忆方向结论

| 方案 | 开源协议 | 独立嵌入 | 自托管 | 时间推理 | 关系查询 | 适用 |
|---|---|---|---|---|---|---|
| Letta 记忆架构 | Apache-2.0 | ✗（需整体采用） | ✓ | 中 | 弱 | 全盘采用 Letta 时 |
| mem0（开源版） | Apache-2.0 | ★ 最强（22+ 框架集成） | ✓ | 中 | 弱（v3 起 graph 仅托管版） | 任意框架的记忆层 |
| Graphiti | Apache-2.0 | 强（Python 库 + MCP） | ✓（需运维图数据库） | ★ 最强（双时间窗口） | ★ 最强 | 需要事实时效与实体关系时 |
| 纯向量库 | — | — | ✓ | 无 | 无 | 仅作知识库 RAG，不作记忆 |

**推荐组合（若不走 Letta 整体方案）**：自己实现 Letta 风格的 core memory（`human` + `persona` 两个常驻 prompt 的记忆块 + agent 自我编辑工具，这是 MemGPT 论文的公开思想，无需引入整个 harness），再以 **mem0 作为 archival/recall 长期存储层**；若需要强时间推理（"用户上次说想换工作是什么时候"）再叠加 Graphiti。

---

## 四、模型无关抽象层

### 4.1 LiteLLM

- GitHub：https://github.com/BerriAI/litellm （~56.1k stars，活跃）。协议：主体 MIT，但 `enterprise/` 目录（SSO、审计日志等）适用独立企业许可。来源：https://github.com/BerriAI/litellm/blob/main/LICENSE
- 两种模式：**Python SDK**（`litellm.completion()`，统一 OpenAI 输入/输出格式、统一异常映射、成本追踪）与 **Proxy 网关**（自托管 OpenAI 兼容端点，virtual keys、预算限流、fallback、Guardrails、MCP 网关）。来源：https://docs.litellm.ai/docs/
- 支持 100+ provider（OpenAI / Anthropic / Gemini / DeepSeek / Groq / Ollama / vLLM / OpenRouter 等）；中央 `model_prices_and_context_window.json` 记录各模型定价、上下文窗口、能力标记（function calling、prompt caching 等）。来源：https://docs.litellm.ai/docs/providers
- Router 提供跨 deployment 负载均衡、cooldown、重试、跨模型 fallback、`context_window_fallback_dict`（超上下文自动切长窗模型）。来源：https://docs.litellm.ai/docs/routing
- 能力探测 API：`supports_function_calling()` 等。来源：https://docs.litellm.ai/docs/completion/function_call

### 4.2 OpenRouter

- 托管统一 API，OpenAI 兼容，实测 403 个模型（2026-08，`/api/v1/models`）。价格透传无加价，收入来自充值手续费。支持 `:free` / `:nitro` / `:floor` 等路由后缀。来源：https://openrouter.ai/docs/faq
- **隐私注意**：请求经 OpenRouter 转发给下游 provider，每个 provider 有自己的日志/留存/训练政策；OpenRouter 自身默认不记录 prompt 内容但数据必经第三方。长期记忆型 Agent 的对话含大量个人信息，不建议作为主路径。来源：https://openrouter.ai/docs/features/privacy-and-logging

### 4.3 框架自带抽象

- **LangChain `init_chat_model`**：字符串分发 + model profile（能力字典来自 models.dev），middleware 可据能力动态行为。来源：https://docs.langchain.com/oss/python/langchain/models
- **Pydantic AI Model/Provider/Profile 三层**：Profile 描述"这类模型的请求该怎么构造"（如 Gemini 的 JSON schema 限制用 schema transformer 解决）；内置 `FallbackModel` 支持跨 provider 顺序 fallback。是三者中对模型能力差异处理得最显式的设计。来源：https://ai.pydantic.dev/models/overview/
- **Mastra**：`"provider/model"` 字符串路由，底层基于 Vercel AI SDK。来源：https://mastra.ai/en/docs/agents/overview

本地模型（Ollama / vLLM / LM Studio）均暴露 OpenAI 兼容端点，与云端模型走同一条通道，差异只体现在能力标志上。来源：https://docs.ollama.com/openai

### 4.4 切换模型时什么会"变"

**抽象层能抹平的**：请求/响应 schema、流式格式、异常类型、usage 与成本核算、可用性差异（fallback/重试）。

**抹不平、需自己处理的**：
- Function calling 质量差异（是否支持、parallel calls、JSON schema 限制）→ 需运行时能力检测 + 切换后回归评测
- 上下文窗口差异 → 窗口变小就要更激进的记忆压缩策略
- Prompt caching 机制差异（OpenAI 自动 / Anthropic 显式 `cache_control` / Gemini cachedContents）→ 换模型后缓存命中率与成本模型都变
- 行为/风格漂移：同一 system prompt 在不同模型上遵循度不同 → 只能靠 eval

**对长期记忆型 Agent 的特殊影响**：
- **Embedding 模型不能随 chat 模型乱换**——换 embedding 等于全量重建向量库，这是记忆一致性最大的坑
- 对话历史中的模型特定产物（thinking blocks、tool_call id 格式）应在持久化层归一化为纯文本 + 标准 tool 消息
- **记忆写入路径建议固定用强模型**（事实抽取/摘要对质量敏感），读取/闲聊路径才允许自由切换

### 4.5 抽象层结论

**推荐：LiteLLM Proxy 作为唯一模型入口，框架只做 agent 逻辑。** 模型切换成为改 `config.yaml` 的运维动作而非代码改动；Ollama 与云端模型同构接入；换 Agent 框架时模型层零改动。极简场景可先用 LiteLLM Python SDK + Router 进程内完成，后续平滑迁移到 proxy。**避免双层抽象**（框架抽象与 LiteLLM SDK 叠加会互相干扰调试）。OpenRouter 可作为 LiteLLM 的一个 provider 挂进去做 fallback，不作主路径。

---

## 五、RAG 与向量存储

### 5.1 向量数据库对比（个人笔记规模：几千~几万篇笔记，chunk 后约 10⁵ 量级向量）

| 方案 | Stars | 形态 | 混合检索（向量+BM25） | 单机个人适用性 |
|---|---|---|---|---|
| LanceDB | ~11.1k | 纯嵌入式（本地目录或 S3），Python/TS | ✅ 内置 FTS（tantivy/BM25）+ RRF 融合，可换 reranker | ★ 最佳候选之一 |
| sqlite-vec | ~8.0k | 纯嵌入式 SQLite 扩展，单文件 | ⚠️ 自身仅暴力 KNN，搭配 SQLite FTS5 自行融合；官方声明 pre-v1 可能有破坏性变更 | ✅ 极简场景最佳 |
| Chroma | ~29.0k | 嵌入式或服务 | ⚠️ 无内置 BM25 排序，仅内容过滤 | ✅ 上手最快 |
| Qdrant | ~33.9k | 服务型（Docker/Cloud），客户端有本地模式 | ✅ 一等公民（dense+sparse + RRF/DBSF） | ⚠️ 单机可用但偏重 |
| pgvector | ~22.6k | PostgreSQL 扩展 | ✅ 向量 + PG 原生全文检索 + RRF | ⚠️ 需已有/愿跑 Postgres |
| Milvus Lite | ~45.6k（总仓库） | pip 嵌入式单文件版 | ✅ dense+sparse 混合 | ✅ Lite 版合适 |

规模直觉：10⁵ 量级向量下，嵌入式方案暴力或轻量索引都是毫秒级，**HNSW 级别的 ANN 在此规模并非必需**。

来源：https://github.com/lancedb/lancedb 、https://docs.lancedb.com/search/hybrid-search 、https://github.com/asg017/sqlite-vec 、https://docs.trychroma.com/docs/querying-collections/full-text-search 、https://qdrant.tech/documentation/concepts/hybrid-queries/ 、https://github.com/pgvector/pgvector 、https://milvus.io/docs/milvus_lite.md

### 5.2 Embedding 模型选择

- **OpenAI text-embedding-3-small**：$0.02/1M tokens，MIRACL 多语言 44.0；large 版 3072 维支持 Matryoshka 截断。来源：https://openai.com/index/new-embedding-models-and-api-updates/
- **Qwen3-Embedding**（Apache-2.0）：0.6B / 4B / 8B 三档，100+ 语言、32K 上下文、可变维度；8B 居 MTEB 多语言榜第一（70.58，2025-06）。中英混合场景当前开源最强；0.6B 可跑在普通笔记本。来源：https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- **BGE-M3**（MIT）：100+ 语言、8192 上下文；一个模型同时产出 dense + sparse + multi-vector，官方建议"混合检索 + re-ranking"管线。中英混合老牌稳妥选择。来源：https://huggingface.co/BAAI/bge-m3
- 排行榜参考 MTEB（注意按 Multilingual/Chinese 子榜筛选）：https://huggingface.co/spaces/mteb/leaderboard
- **成本不是决策因素**：几万篇笔记一次性嵌入约数千万 token，text-embedding-3-small 总成本约 $1 量级。真正的权衡是隐私（本地 Qwen3/BGE-M3）vs 免运维（OpenAI API）。
- **换 embedding 模型必须全量重建索引**，选定前用小样本测检索质量。

### 5.3 RAG 框架与可借鉴项目

- **LlamaIndex**（~51.6k，MIT）：`MarkdownNodeParser`、`HierarchicalNodeParser` + `AutoMergingRetriever` 直接适用于笔记场景。来源：https://developers.llamaindex.ai/python/framework/module_guides/loading/node_parsers/modules/
- **LangChain `MarkdownHeaderTextSplitter`**：按标题层级切分并把标题路径写入 metadata。来源：https://python.langchain.com/api_reference/text_splitters/markdown/langchain_text_splitters.markdown.MarkdownHeaderTextSplitter.html
- **khoj**（~36.5k，AGPL-3.0）：专门面向个人笔记（Markdown/org-mode/PDF/Notion）的开源项目，有 Obsidian 插件，支持本地/在线 LLM——"个人笔记 RAG"最直接的对标，架构和 chunking 实现值得借鉴。来源：https://github.com/khoj-ai/khoj
- 建议：个人项目不必引入整个框架，自写 ingestion（约百行代码）+ 直接用向量库 SDK 更可控；框架的 chunking 组件可单独用。

### 5.4 Markdown 笔记 Chunking 要点

1. **按标题层级切分**，标题路径（如 `Header 1/Header 2`）作为 metadata 挂在每个 chunk 上——检索时可过滤、可拼回上下文
2. 标题切分后**再做长度兜底**（与 SentenceSplitter 链式组合）
3. **frontmatter（tags/date/aliases）解析为结构化 metadata** 而非嵌入正文，用于过滤和展示来源
4. 进阶：HierarchicalNodeParser（多级 chunk + 父子回并）适合长笔记；语义切分中文需调阈值，个人笔记规模收益有限

### 5.5 RAG 方向结论

- **首选组合**：LanceDB（嵌入式）+ 本地 embedding（中英混合选 Qwen3-Embedding-0.6B，追求上限选 4B 或 BGE-M3）+ 标题层级切分 + frontmatter 解析。一个 `pip install lancedb` 同时得到向量检索、BM25 全文索引和 RRF 混合检索，零服务进程。
- **更极简**：sqlite-vec + SQLite FTS5（接受 pre-v1 风险）。
- **不想跑本地模型**：embedding 换 OpenAI text-embedding-3-small，其余不变。
- **什么时候需要 Qdrant / pgvector**：多应用/多设备并发访问同一索引（嵌入式基本是单写者）、向量规模百万级以上、已有 PostgreSQL 且需与关系数据 JOIN。个人笔记场景基本碰不到——**服务型数据库解决的是并发和规模问题，不是检索质量问题**。

---

## 六、Skills / 能力扩展机制

### 6.1 Anthropic Agent Skills（已成开放标准）

- **2025-12-18，Agent Skills 被发布为开放标准**，规范全文在 https://agentskills.io/specification 。来源：https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- 结构：一个 skill 就是一个目录，最小只需一个 `SKILL.md`（YAML frontmatter + Markdown 正文）。必填字段仅 `name`（≤64 字符）和 `description`（≤1024 字符，写清"做什么+何时用"）。可选子目录：`scripts/`（可执行代码）、`references/`（按需加载的参考文档）、`assets/`（静态资源）。
- **Progressive disclosure（渐进式披露）三阶段**：Discovery（启动时只注入 name+description，~100 tokens/skill）→ Activation（任务匹配时 agent 用文件读取工具读入 SKILL.md 全文，建议 <5000 tokens）→ Execution（按需读 references 或直接执行 scripts，理论上无上限）。关键洞察：触发机制**纯 prompt 驱动**，无特殊协议；脚本直接执行只取结果，不必读入上下文。
- 官方示例仓库：https://github.com/anthropics/skills （Apache-2.0）。安全提示：skill 含指令+可执行代码，只安装可信来源。

### 6.2 MCP（Model Context Protocol）

- 基于 JSON-RPC 2.0，Host → Client → Server 架构；三种原语：**Tools**（模型调用）、**Resources**（上下文数据）、**Prompts**（模板化工作流）。来源：https://modelcontextprotocol.io/specification/2025-06-18
- 传输：stdio（子进程）与 Streamable HTTP（2025-03 起取代 HTTP+SSE，支持会话管理与断线恢复）。Auth 基于 OAuth 2.1，仍在快速演进。来源：https://modelcontextprotocol.io/specification/2025-06-18/basic/transports 、https://modelcontextprotocol.io/specification/2025-11-25/changelog
- 生态规模：官方 Registry（https://registry.modelcontextprotocol.io/ ）实测 **≥6000 个已发布 server**（2026-08 分页实测下限，仍在增长）；官方 SDK 覆盖 10 种语言。官方 servers 仓库已收缩为 7 个参考实现。来源：https://github.com/modelcontextprotocol/servers

### 6.3 Function calling 生态

"name + description + JSON Schema 参数"三元组已成事实标准（OpenAI、Anthropic、MCP tools 同构）。新动向：OpenAI Agents SDK 的 **tool search / defer_loading**（大量工具不常驻上下文，模型按需加载）——与 Skills 的渐进式披露是同源思路在 tool 层的应用。来源：https://openai.github.io/openai-agents-python/tools/

### 6.4 Kimi Code CLI 的 Skills 机制

- **直接支持 agentskills.io 开放格式**，机制与 Anthropic 完全一致（发现 → 注入元数据 → agent 自行读 SKILL.md）。来源：https://raw.githubusercontent.com/MoonshotAI/kimi-cli/main/docs/en/customization/skills.md
- 亮点：**跨工具兼容目录**——同时扫描 `~/.kimi/skills/`、`~/.claude/skills/`、`~/.codex/skills/`、`~/.config/agents/skills/` 等；分层优先级 Project > User > Extra > Built-in，同名覆盖；支持扁平 .md 文件降低创建门槛。
- 官方区分 Skills（SKILL.md 知识性引导）vs Plugins（plugin.json 声明可执行工具）；内置 `skill-creator` 元 skill 教 agent 自己创建 skill——**自我扩展的落地实例**。
- 另有 flow skills（frontmatter `type: flow`，正文嵌 Mermaid 流程图，agent 按 BEGIN→END 自动执行多轮工作流）——与需求 7（专业工作流）直接相关。

### 6.5 其他参考设计

- **Letta**：工具为服务端托管对象，可跨 agent 复用；工具沙箱内可反过来调用 Letta API 管理自己的 memory blocks、创建 subagent（"工具内自我扩展"）。来源：https://docs.letta.com/guides/agents/custom-tools
- **Claude Agent SDK**：自定义工具 = 进程内 SDK MCP server（`@tool` 装饰器 + `create_sdk_mcp_server()`），与外部 MCP server 混用；subagents/hooks/skills 继承自 Claude Code 运行时。来源：https://github.com/anthropics/claude-agent-sdk-python
- **OpenAI Agents SDK**：`agent.as_tool()`（子 agent 以工具形式被编排）与 handoffs（完整移交控制权）两种多 agent 模式；OpenAI 也已引入 skills 概念——格式正在跨厂商渗透。

### 6.6 Skills 方向结论

**Skills 与 MCP 的互补关系**：

| | Skills | MCP |
|---|---|---|
| 本质 | 文件包：知识 + 工作流 + 可选脚本 | 协议：进程间工具/数据服务 |
| 回答的问题 | "**怎么做**"——procedural knowledge、领域流程 | "**用什么做**"——确定性能力、外部系统连接 |
| 上下文成本 | 渐进披露，闲置时 ~100 tokens/skill | 工具 schema 常驻上下文 |
| 状态 | 无状态，纯文件 | 有状态连接、会话、鉴权 |

Anthropic 工程博客明确："Skills can complement MCP servers by teaching agents more complex workflows that involve external tools"——MCP 提供原语工具，Skills 教 agent 何时、按什么顺序、以什么规范使用。**需求 7 的专业工作流（需求→产品→研发→测试→上线）正是 Skills 的典型用途**（写成 SKILL.md 或 Kimi 式 flow skill），需求 5 的子智能体对接则走 MCP / agents-as-tools。

**自建基座的最小可行 skills 实现**（从 Claude Code / Kimi Code 反推，全部纯文件操作、零协议）：

1. **发现**：扫描固定目录（`~/.config/agents/skills/` + 项目级 `.agents/skills/`），解析 `*/SKILL.md` 的 frontmatter
2. **注入**：把 name + description + 路径拼进 system prompt（~100 tokens/skill）
3. **激活**：agent 已有文件读取工具，让它自己 Read SKILL.md——无需任何特殊机制
4. **执行**：保证 agent 有 shell/代码执行工具，SKILL.md 里相对路径引用 scripts/references

加强项（按优先级）：目录优先级覆盖、扁平 .md 支持、`/skill:<name>` 手动触发兜底、`skill-creator` 元 skill（让 agent 把成功工作流沉淀成新 skill）、第三方 skill 安全审计（含 prompt injection 面）。

---

## 七、自我成长机制

### 7.1 已有成熟实践

**Letta self-editing memory**：agent 通过 tool 调用修改自己的 `persona` block（自我概念、性格、行为准则）和 `human` block（用户画像）——这就是"agent 更新自己 system prompt/人格"的官方成熟实践。新版 MemFS 将其泛化为 git 版本化的文件式记忆 + `/remember` 显式教学 + `/doctor` 记忆审计。来源：https://www.letta.com/blog/memory-blocks 、https://docs.letta.com/guides/agents/overview

**Sleep-time compute / dreaming**（对话沉淀为长期记忆的最成熟方案）：
- 双 agent 分工：primary agent 对话（快模型，无记忆编辑工具）+ sleep-time agent 异步整理记忆（强模型，能改共享 memory blocks），解决"对话中做记忆操作又慢又乱"的痛点。来源：https://www.letta.com/blog/sleep-time-compute
- **直接支持文档消化**：上传数据源后 sleep-time agent 后台通读并把重要发现写进 primary 的记忆，"anytime" 方式——primary 不用等它读完。这是"喂书给 agent"方向最贴近的现成机制。
- 论文数据：同等精度下 test-time compute 降约 5x。来源：https://arxiv.org/abs/2504.13171
- 同类：LangMem 的 background memory manager（https://langchain-ai.github.io/langmem/ ）、mem0 的异步抽取管线、Graphiti 的增量 episode 摄入。

**Generative Agents（Stanford 小镇）**：检索三因子（recency 指数衰减 + importance LLM 打分 + relevance embedding 相似度）与"importance 累计超阈值触发反思（reflection）→ 形成反思树"两个设计被后续无数项目照抄，实现成本低。但代码是 2023 年研究原型，不是可复用库。来源：https://arxiv.org/abs/2304.03442 、https://github.com/joonspk-research/generative_agents

**Voyager（Minecraft 终身学习）**：持续增长的 skill library——技能以可执行代码形式存向量库、可组合、有 self-verification 入库校验环节。启示：**"提炼出的思维框架"应当带描述/索引、可检索、有入库校验，而不是无脑堆进 prompt**。来源：https://arxiv.org/abs/2305.16291 、https://github.com/MineDojo/Voyager

### 7.2 半成熟与论文阶段

- **A-MEM（Agentic Memory）**：按 Zettelkasten 方法组织记忆——新记忆生成结构化笔记（上下文描述+关键词+标签），自动与历史记忆建立链接，新记忆可触发旧记忆演化。非常适合"书摘→思维卡片→知识网络"，但仍是论文+研究代码阶段（NeurIPS 2025）。来源：https://arxiv.org/abs/2502.12110
- **GEPA（DSPy 生态）**：反思式 prompt 进化，采样 trajectory → 自然语言反思 → 更新 prompt，ICLR 2026 Oral 且代码开源——但需要评估集/反馈信号驱动，"管家是否更懂你"缺乏现成 metric。来源：https://arxiv.org/abs/2507.19457
- **Second Me**：把笔记/文档喂进去**微调**出"AI 分身"（本地训练），是与人格化记忆完全不同的技术路线，工程门槛高。来源：https://github.com/mindverse/Second-Me
- **Memobase**：按用户维度持续构建画像的长期记忆后端（README 调研时 404，细节未核实）。来源：https://docs.memobase.io/introduction

### 7.3 关键空白点

**没有找到把"读书→提炼思维框架→回写 system prompt/persona"作为主打功能的成熟开源项目。** 最接近的是 Letta sleep-time 文档消化（产物进记忆，不进 persona）。这个回写动作在机制上是现成的（Letta 里就是自定义一个改 persona block 的 tool），但"提炼框架"的 prompt 流程与质量控制需要自己设计——建议参考 Voyager 的 self-verification 入库校验思路。

### 7.4 自我成长方向结论

- **(a) 对话沉淀为长期记忆**：成熟。照抄 Letta sleep-time 双 agent 分工或 LangMem background manager；检索侧用 Generative Agents 三因子。
- **(b) 读书提炼思维框架入库**：半成熟，需组合。现成件：Letta sleep-time 文档消化、Graphiti episode 摄入；组织方式参考 A-MEM；入库质控参考 Voyager。
- **(c) 自动更新用户认知档案**：成熟。最简单实现就是可编辑、有大小上限、常驻上下文的 `human` block；规模上去后接 mem0/Graphiti。

---

## 八、综合推荐架构

### 8.1 首选方案：Letta Harness 为基座

```
┌─────────────────────────────────────────────────┐
│  渠道层：CLI / Telegram / Slack / Web（Letta 自带） │
├─────────────────────────────────────────────────┤
│  Letta Harness（letta-code，TS）                  │
│  ├─ 主 Agent（对话，快模型）                       │
│  ├─ Sleep-time / dreaming 子 Agent（记忆整理，强模型）│
│  ├─ MemFS（git 版本化记忆文件系统）                 │
│  ├─ Skills（agentskills.io 格式）+ MCP + mods     │
│  └─ Schedules（定时任务，驱动自我成长节奏）          │
├─────────────────────────────────────────────────┤
│  知识库 RAG（自建，作为 MCP server / skill 挂载）：   │
│  LanceDB + Qwen3-Embedding（本地）+ 标题层级切分     │
├─────────────────────────────────────────────────┤
│  模型入口：LiteLLM Proxy（统一 OpenAI 兼容端点）     │
│  OpenAI / Anthropic / Gemini / DeepSeek / Ollama  │
└─────────────────────────────────────────────────┘
```

**理由**：
1. 需求 2/3/6（长期记忆、人格化、自我成长）是 Letta 的产品核心，memory blocks + MemFS + dreaming 开箱即用，省去自建记忆架构的最大工程量
2. 需求 4（模型无关）：Letta 原生支持全主流 provider；再叠 LiteLLM Proxy 作为统一入口，切换模型是运维动作
3. 需求 5（skills 扩展）：Letta 原生支持 skills（兼容 agentskills.io 生态）+ MCP + mods
4. 需求 7（专业工作流）：写成 SKILL.md / flow skill 挂载
5. 需求 1（笔记 RAG）：Letta 的 archival memory 可承担一部分，但结构化笔记库建议自建 LanceDB RAG 管线（chunking 可控、frontmatter 元数据可过滤），以 MCP server 形式挂载
6. 可完全自托管，数据不出本地，契合"私人"定位

**代价与对策**：Letta 正处 letta → letta-code 迁移期——锁定版本、关注官方迁移指南；harness 范式较重——但本需求本来就是"常驻管家"，范式匹配。

### 8.2 备选方案：Pydantic AI + mem0 自建（Python 轻量路线）

适合想完全掌控每一层、不依赖重型 harness 的情况：

- **Agent 内核**：Pydantic AI（模型无关最彻底，25+ provider 字符串切换，统一消息模型，durable execution 适合常驻进程）。TS 栈则换 Mastra（自带 Observational Memory，可省掉部分记忆自建工作）
- **Core memory（自建，~200 行代码）**：`human` + `persona` 两个 memory block 常驻 system prompt，配 `update_core_memory` 工具让 agent 自我编辑（MemGPT 论文公开思想）
- **长期记忆层**：mem0 开源版自托管（框架无关、Python/TS SDK、可换本地提取模型）；需要时间推理/实体关系时叠加 Graphiti
- **自我成长**：定时任务（cron）跑"整理 agent"——把当日对话经 mem0 抽取管线沉淀，触发阈值时做 Generative Agents 式反思，结论回写 human/persona block
- **Skills**：按 §6.6 的最小可行实现自建（目录扫描 + prompt 注入 + 文件读取激活，约百行代码），兼容 agentskills.io 格式
- **RAG**：LanceDB + Qwen3-Embedding-0.6B + MarkdownHeaderTextSplitter 式标题切分 + frontmatter 解析
- **模型入口**：LiteLLM Proxy

**代价**：记忆架构、自我成长循环、skills 机制都要自建（估计核心原型 1-2 周），换来的是零黑盒、每层可替换。

### 8.3 两条路线的共同决策（无论选哪条）

- **Embedding 模型选定后不要轻易换**（换 = 全量重建索引）；中英混合笔记首选 Qwen3-Embedding-0.6B 本地跑
- **记忆写入路径固定用强模型**（事实抽取/反思对质量敏感），闲聊路径才允许自由切模型
- **对话历史持久化时归一化**为纯文本 + 标准 tool 消息，避免模型私有格式（thinking blocks、tool_call id）污染跨模型续跑
- **知识库（RAG）与记忆（memory）分两层**：RAG 管"用户的笔记内容"，记忆管"agent 对用户的认知"，不要用向量库同时充当两者

---

## 九、风险与注意事项

1. **Letta 产品线迁移风险**：老 server（letta-ai/letta）已标 legacy，活跃开发在 letta-code（TS）；core/archival/recall 三层术语属于 legacy V1，新形态是 MemFS 文件记忆。选择 Letta 路线需接受这一过渡期不确定性，建议锁定版本并跟踪 https://docs.letta.com 的迁移指南。
2. **记忆系统的开源/托管分化**：mem0 v3 起 graph memory 仅托管版；Zep 本体已闭源只留 Graphiti 开源。选型时以"开源版实际能力"为准，不要被托管版 benchmark 数字误导（mem0 官方明确说开源版数字不同）。
3. **Graphiti 的提取质量依赖模型 Structured Output 能力**，换小模型/弱模型可能导致构图失败——记忆链路模型选择要保守。来源：https://github.com/getzep/graphiti
4. **OpenRouter 隐私**：数据经第三方转发且下游 provider 链不透明，私人管家的对话高度敏感，不建议主路径使用。来源：https://openrouter.ai/docs/features/privacy-and-logging
5. **Skills 安全面**：skill 含指令+可执行代码，第三方 skill 存在 prompt injection 风险；安装前审计、危险脚本走人工确认。来源：https://github.com/anthropics/skills
6. **MCP 生态质量参差**：Registry 6000+ server 中质量与安全水平不一，生产使用前逐个评估；auth 规范（OAuth 2.1 组合）仍在快速演进。
7. **"喂书形成思维模型"是工程空白**：没有成熟项目做"提炼框架→回写 persona"这一步，需自行设计提炼 prompt 与入库校验（参考 Voyager self-verification），并做好"提炼质量差导致人格漂移"的回滚机制（MemFS 的 git 版本化正好兜底）。
8. **自我评估难题**：GEPA 类 prompt 自我进化需要评估集驱动，"管家是否更懂你"缺乏客观 metric——建议以人工定期审阅 memory blocks（Letta 的 `/doctor` 思路）代替全自动进化，至少在早期。
9. **sqlite-vec pre-v1**：官方声明可能有破坏性变更，选它做存储要接受升级成本。来源：https://github.com/asg017/sqlite-vec
10. **微调路线（Second Me 式"AI 分身"）与记忆路线是互斥的技术押注**：本报告推荐记忆路线（可解释、可编辑、可回滚、工程门槛低）；微调路线需要本地训练资源且行为不可控，仅作为远期探索。

---

## 附：主要一手来源索引

**框架**
- LangChain v1 release notes: https://docs.langchain.com/oss/python/releases/langchain-v1
- Letta docs: https://docs.letta.com ｜ https://github.com/letta-ai/letta-code
- CrewAI memory: https://docs.crewai.com/en/concepts/memory
- Microsoft Agent Framework: https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework-the-open-source-engine-for-agentic-ai-apps/
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
- Claude Agent SDK: https://github.com/anthropics/claude-agent-sdk-python
- Pydantic AI: https://ai.pydantic.dev/
- Mastra: https://mastra.ai/docs ｜ https://mastra.ai/docs/memory/overview

**记忆**
- MemGPT 论文: https://arxiv.org/abs/2310.08560
- Letta memory blocks: https://www.letta.com/blog/memory-blocks/
- Sleep-time compute: https://www.letta.com/blog/sleep-time-compute ｜ https://arxiv.org/abs/2504.13171
- mem0: https://github.com/mem0ai/mem0 ｜ https://docs.mem0.ai
- Graphiti: https://github.com/getzep/graphiti ｜ https://arxiv.org/abs/2501.13956
- RAG vs memory: https://www.letta.com/blog/rag-vs-agent-memory/

**抽象层 / RAG / Skills / 自我成长**
- LiteLLM: https://docs.litellm.ai/docs/ ｜ OpenRouter: https://openrouter.ai/docs/faq
- LanceDB: https://docs.lancedb.com/search/hybrid-search ｜ Qwen3-Embedding: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B ｜ BGE-M3: https://huggingface.co/BAAI/bge-m3 ｜ khoj: https://github.com/khoj-ai/khoj
- Agent Skills 规范: https://agentskills.io/specification ｜ Anthropic 工程博客: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- MCP spec: https://modelcontextprotocol.io/specification/2025-06-18 ｜ Registry: https://registry.modelcontextprotocol.io/
- Kimi CLI skills: https://raw.githubusercontent.com/MoonshotAI/kimi-cli/main/docs/en/customization/skills.md
- Generative Agents: https://arxiv.org/abs/2304.03442 ｜ Voyager: https://arxiv.org/abs/2305.16291 ｜ A-MEM: https://arxiv.org/abs/2502.12110 ｜ GEPA: https://arxiv.org/abs/2507.19457

**调研局限说明**：Claude 官方文档站（docs.claude.com 等）与 OpenAI 平台文档（platform.openai.com）调研时网络访问失败，相关内容改引对应 GitHub README / openai-cookbook 一手仓库；Memobase README 404 未核实细节；LangGraph memory 概念页细节未二次确认；star 数为 2026-08-11 GitHub API 实测值。
