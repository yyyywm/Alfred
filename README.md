# Alfred —— 你的私人管家 AI

> 像蝙蝠侠的 Alfred 一样：了解你的一切，记得你说过的每件事，读你读过的书，陪你做决定。

Alfred 是一个以**长期记忆**和**个人知识库**为核心的私人智能体。它认识你、记得你的
经历与偏好、能调用你的笔记回答问题、可以用书籍喂养出思维框架，能力可通过 skills
持续扩展，并且模型无关——接入任何厂商的 API 都不影响它的行为。

技术选型调研：`docs/research/2026-08-personal-butler-agent-tech-selection.md`
实施计划：`docs/plans/2026-08-implementation-plan.md`

## 架构

```
┌──────────────────────────────────────────────┐
│ CLI：chat / ingest / feed / consolidate /     │
│ memory / skills / models / frameworks         │
├──────────────────────────────────────────────┤
│ Agent 内核（Pydantic AI）                      │
│  System prompt 三层组装（KV-cache 友好）：       │
│   ① 静态：人格 + 行为准则 + 工具准则             │
│   ② 半静态：human/persona 记忆块 + 常驻规则      │
│   ③ 动态：skills 索引 + 日期                    │
│  恒定工具集：notes_search / memory_search /     │
│   memory_update_block / file_read / shell /    │
│   run_python                                  │
├──────────────────────────────────────────────┤
│ 记忆层（它对你的认知）        知识层（你的笔记）  │
│  ├─ human/persona blocks      ├─ LanceDB RAG   │
│  │  （常驻、git 版本化）       │  （标题切分）    │
│  ├─ mem0 长期记忆（异步）      └─ 思维框架库     │
│  ├─ 情景记忆（成功案例）         （feed 提炼）   │
│  └─ consolidate 睡眠整理                       │
├──────────────────────────────────────────────┤
│ 模型入口：config.yaml 声明式配置，换模型不改代码 │
└──────────────────────────────────────────────┘
```

## 设计哲学

每个子系统都有主流生产级 agent 验证过的设计做依据（详见调研报告）：

- **记忆与知识分两层**（Letta）：向量库管笔记内容，记忆系统管"它对你的认知"，互不替代
- **记忆召回三层混合**（Letta + LangMem + ChatGPT Memory）：profile 常驻 +
  按需工具召回（硬预算 ≤10 条，相关性+近因排序）+ 后台整理晋升
- **丢内容留指针**（Manus）：上下文压缩保留文件路径/出处，随时可恢复
- **最小高信号 token 集**（Anthropic context engineering）：memory blocks 设字符上限，
  "塞得下"不等于"该塞"
- **渐进披露**（Anthropic Agent Skills）：技能元数据常驻 ~100 tokens，正文按需读取
- **复盘→草稿→确认→git 版本化**（Devin Knowledge）：自我沉淀不做全自动无确认改写
- **确定性规则代码层强制**（Claude Code hooks）：persona 修改、shell/python 执行必须
  用户确认，不靠 prompt 约束

## 安装

```bash
# 环境：conda base（Python 3.13）
source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh
conda activate base

pip install -e ".[dev]"
cp .env.example .env   # 填入你的 API key（默认已从本地 Kimi Code 配置读取）
```

## 快速开始

```bash
alfred models            # 检查配置的模型与 key 可用性
alfred chat              # 开始对话
alfred ingest ~/notes    # 索引你的笔记目录（首次下载 embedding 模型 ~600MB）
alfred feed book.md      # 喂养一本书，提炼思维框架
alfred frameworks 决策    # 检索已提炼的思维框架
alfred consolidate       # 睡眠整理：复盘近期对话，确认后沉淀记忆
alfred memory list       # 查看长期记忆
alfred skills            # 查看技能与规则
```

chat 内斜杠命令：

| 命令 | 作用 |
|---|---|
| `/new` | 开启新会话 |
| `/model provider:model` | 切换闲聊模型 |
| `/remember <内容>` | 显式教学，写入用户画像 |
| `/memory` | 查看长期记忆 |
| `/why` | 查看上一轮回答依据了哪些记忆 |
| `/sessions` | 列出历史会话 |
| `/exit` | 退出 |

## 配置（config.yaml）

```yaml
providers:
  kimi-for-coding:           # 任何 Anthropic / OpenAI 兼容端点都能接
    type: anthropic
    base_url: https://api.kimi.com/coding
    env_key: KIMI_API_KEY
    models: [k3]

models:
  chat: kimi-for-coding:k3          # 闲聊路径：对话中可用 /model 自由切
  memory_write: kimi-for-coding:k3  # 记忆写入路径：固定强模型，质量敏感
  embed:
    name: Qwen/Qwen3-Embedding-0.6B # 本地 embedding：选定后不要换（换=重建索引）
```

## 扩展能力

- **加技能**：`skills/<名字>/SKILL.md`（frontmatter 写 name/description + 流程正文，
  推荐 Playbook 分节：Procedure / Specifications / Advice / Forbidden Actions /
  Required from User），Alfred 在任务匹配时自动激活
- **加规则**：`rules/*.md`（frontmatter 四触发器：`alwaysApply` 常驻 /
  `description` 智能召回 / `globs` 匹配 / 手动）
- **自我成长**：日常对话自动沉淀长期记忆；定期 `alfred consolidate` 复盘产出
  记忆/规则/skill 草稿，确认后入库（git 可回滚）；`alfred feed` 喂养书籍提炼框架

## 隐私与安全

- 所有记忆、笔记索引、会话历史全部存在本地 `data/` 目录
- mem0 遥测已关闭；唯一的对外流量是你配置的 LLM API
- 记忆对你完全透明：`alfred memory list/delete`、`/why` 可随时审计

## 测试

```bash
python -m pytest tests/ -q   # 26 个单元测试，不依赖真实 LLM 调用
```
