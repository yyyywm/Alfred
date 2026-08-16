# Alfred —— 你的私人管家 AI

> 一个会主动思考、从对话中提炼教训、持续成长的私人 AI。
> 它能阅读你的笔记、吸收你喂给它的知识、记住你说的每件事，
> 而且**能修改自己的代码来进化**——在人类的监督下。

Alfred 是一个以**长期记忆**和**个人知识库**为核心的私人智能体：

- **认识你**：跨会话记住你的经历、偏好、思维方式，越用越懂你
- **读你的笔记**：索引个人笔记库，回答问题时引用出处
- **可以喂养**：喂给它书籍文章，提炼成思维框架，成为它思考的工具
- **会反思成长**：遇到失败和纠正时自动提炼教训（RefleXion 机制），下次类似场景自动激活
- **情景记忆**：记住"上次怎么成功解决了 X 问题"，下次遇到类似情况自动召回
- **自我进化**：在人类监督下修改自己的源代码（`code_patch`），持续改进
- **模型无关**：任何 OpenAI / Anthropic / Gemini 兼容的 API 都能接，换模型不改代码

## 项目能做什么

### 作为个人助手（日常使用）

| 场景 | 怎么用 |
|---|---|
| 聊天对话 | `alfred chat`，就像和聪明的朋友说话 |
| 让它记住你 | 直接聊天，它会自动提炼记忆；或用 `/remember` 显式教学 |
| 查你的笔记 | 索引笔记后，问它"我之前写的 XX 笔记说了什么" |
| 学习新知识 | 把书/文章喂给它：`alfred feed book.md`，它提炼成思维框架 |
| 复盘成长 | `alfred consolidate` 让它从对话中提炼教训，下次用得上 |

### 作为你的代码伙伴（编程使用）

| 场景 | 怎么用 |
|---|---|
| 代码问答 | 在 chat 中问它关于代码的问题，它能读取项目文件 |
| 代码修改 | 让它用 `code_patch` 工具改代码（路径/语法/测试三重门禁，你确认后执行） |
| 运行验证 | 它可以用 `shell` 和 `run_python` 工具跑测试、验证修改 |

### 作为长期资产（持续使用）

| 场景 | 怎么用 |
|---|---|
| 知识沉淀 | 你的笔记 + 喂书提炼的框架 + 对话中积累的记忆，都在 `data/` 目录 |
| 多 agent 共享 | 记忆层支持 `user_id` 隔离，多个 AI agent 可共享同一套记忆基础设施 |
| 云端迁移 | 记忆客户端可插拔，未来可直接接入云端服务（如 mem0 云） |

## 安装

环境要求：Python 3.11+

### 使用 conda（推荐）

```bash
conda env create -f environment.yml
conda activate alfred
cp .env.example .env   # 填入至少一个 LLM API key
```

### 使用 pip

```bash
pip install -e ".[dev]"
cp .env.example .env
```

验证安装：

```bash
alfred models    # 应列出配置的模型，而非报 ModuleNotFoundError
```

## 快速开始

```bash
alfred models                # 检查模型与 key 配置
alfred chat                  # 开始对话
alfred ingest ~/notes        # 索引笔记目录（首次需下载 embedding 模型 ~600MB）
alfred feed book.md          # 喂养一本书，提炼思维框架
alfred consolidate           # 睡眠整理：复盘近期对话，提炼记忆与教训
alfred memory list           # 查看长期记忆
alfred skills                # 查看技能与规则
```

## Chat 交互

### 启动选项

```bash
alfred chat                # 正常对话
alfred chat --debug        # 启用调试日志，同时输出到控制台
alfred chat -s <session>   # 恢复指定会话
```

### 交互行为

- 输入支持行编辑：方向键移动光标、Backspace/Delete、Home/End、上下翻阅历史
- 发送后显示 `助手正在思考...` 状态提示，收到回复后切换为 `助手：` 前缀
- Alfred 的回复以 Markdown 渲染
- 工具调用单独成行显示（如 `🔧 memory_search ✓`）
- 按 `Ctrl-C` 中断当前回复，不会退出对话
- 对话日志写入 `data/logs/alfred.log`（5MB 轮转 ×3），`--debug` 时同时输出到控制台

### 斜杠命令

| 命令 | 作用 |
|---|---|
| `/new` | 开启新会话 |
| `/model <provider:model>` | 切换闲聊模型 |
| `/remember <内容>` | 显式教学，写入用户画像（human 块） |
| `/memory` | 查看长期记忆 |
| `/why` | 查看上一轮回答依据了哪些记忆 |
| `/sessions` | 列出历史会话（带序号） |
| `/load <序号或id>` | 加载历史会话，继续之前的上下文 |
| `/delete <序号或id>` | 删除会话记录（需确认） |
| `/lessons` | 查看管家从过去中学到的教训（RefleXion 教训库） |
| `/whoami` | 查看 Alfred 的积累状态仪表盘 |
| `/status` | 检查当前模型与 embedding 连接状态 |
| `/exit` 或 `/quit` | 退出 |

## 记忆系统

### ① 常驻记忆块（human / persona / lessons）

三个 Markdown 文件，存储于 `data/memory/`，git 版本化，每次修改自动 commit，可回滚。

| 块 | 内容 | 修改需确认？ |
|---|---|---|
| `human.md` | 管家对用户的认知（经历、偏好、思维方式） | ✅ 需确认 |
| `persona.md` | 管家的自我设定（性格、原则、行为准则） | ✅ 需确认 |
| `lessons.md` | 从过去失败/纠正中提炼的教训（追加型，自动激活） | ❌ 自动写入 |

- 每个块有字符上限（默认 2000 字符，lessons 4000 字符），超过上限时会拒绝写入或自动压缩
- 使用 `/remember` 可直接向 human 块追加一条事实
- 使用 `alfred memory history human` 或 `alfred memory history persona` 查看版本历史

### ② 长期记忆（mem0）

跨会话的事实沉淀。每轮对话结束后自动抽取，存入本地 Qdrant 向量库。

- 过滤琐碎消息（"好的"、"嗯"、"哈哈"等短回复）不写入
- 支持相关性 + 近因度混合排序，每轮召回硬预算 10 条
- `alfred memory list` 查看全部，`alfred memory delete <id>` 删除指定记忆

### ③ 情景记忆（episodes）

记录"成功的案例"——场景、思路、行动、结果四元组，存 LanceDB。Alfred 遇到类似场景时自动检索，借鉴之前的做法。

### RefleXion 教训机制

遇到工具调用失败、用户纠正、操作被拒绝时，Alfred 会自动（或经 `alfred consolidate` 复盘时）提炼为一条教训，追加到 `lessons.md`。下次遇到类似场景时，教训自动注入系统 prompt，指导决策。

使用 `/lessons` 查看所有教训，支持按类别过滤（如 `/lessons code-debug`）。

## 知识系统

### 索引笔记

```bash
alfred ingest ~/notes
```

- 增量索引 Markdown 目录，文件 hash 判断变更，相同文件不会重复索引
- 按标题层级切分片段，保留标题路径前缀
- 查询时自动引用出处

### 喂养书籍/文章

```bash
alfred feed book.md
```

- 分段通读，提炼思维框架卡片（含名称、来源、正文、标签）
- 入库前四要素校验，不合格不入库
- 用 `alfred frameworks <query>` 检索已提炼的框架

## 配置（config.yaml）

```yaml
# 声明式 provider 配置
providers:
  deepseek:
    type: openai_compat
    base_url: https://api.deepseek.com
    env_key: DEEPSEEK_API_KEY
    models: [deepseek-chat, deepseek-reasoner]
  kimi-for-coding:
    type: anthropic
    base_url: https://api.kimi.com/coding
    env_key: KIMI_API_KEY
    models: [k3, k2p5]

models:
  chat: deepseek:deepseek-chat          # 闲聊模型，对话中 /model 自由切
  memory_write: kimi-for-coding:k3       # 记忆写入路径：固定强模型，质量敏感
  embed:
    provider: local                      # local（本地 sentence-transformers）或 openai_compat（云端 API）
    name: Qwen/Qwen3-Embedding-0.6B
    # 当 provider: openai_compat 时启用：
    # base_url: https://api.siliconflow.cn/v1
    # env_key: SILICONFLOW_API_KEY

memory:
  dir: data/memory
  block_char_limit: 2000
  recall_budget: 10
  recency_half_life_days: 30
  provider: local                        # 记忆客户端 provider（当前仅 local）
  default_user_id: owner                 # 默认用户 id，多 agent 共享时用于隔离

paths:
  history_dir: data/history
  vectordb_dir: data/vectordb
  skills_dirs: [skills, ~/.config/alfred/skills]
  rules_dirs: [rules, ~/.config/alfred/rules]
```

### 关键配置原则

- **模型无关**：代码里不硬编码任何模型名或 API 地址，全部走 `config.yaml`
- **换 embedding 模型必须重建索引**：`models.embed.name` 一旦确定不要轻易更换，否则笔记/框架/情景记忆向量库需全量重建
- **memory_write 固定强模型**：记忆抽取和复盘质量敏感，不要用便宜模型

## 扩展机制

### Skills（技能）

在 `skills/<name>/SKILL.md` 创建技能文件，含 YAML frontmatter（`name`、`description`）+ Markdown 正文，符合 Anthropic Agent Skills 标准格式。

- **索引注入**：启动时将每个 skill 的 name + description 注入系统 prompt，LLM 自行判断当前任务是否匹配
- **零侵入**：不要求任何额外字段，任何第三方 skill 拿来即用
- 扫描目录由 `paths.skills_dirs` 控制

### Rules（规则）

在 `rules/*.md` 创建规则文件，含 YAML frontmatter。

- `alwaysApply: true`：常驻注入系统 prompt
- `description`：进入可召回索引，agent 按需读取
- 扫描目录由 `paths.rules_dirs` 控制

## 自我进化

Alfred 拥有 `code_patch` 工具，可以修改自己的源代码来进化。这是基于学术研究的代码自修改范式：

- **CodeAct**（Wang et al., ICML 2024）：用可执行代码作为 agent 的统一动作空间
- **SWE-bench**（Jimenez et al., 2023）：生成 patch → 测试验证的标准流程

### 三重门禁

每次 `code_patch` 调用都会经过：

1. **路径门禁**：只允许修改 `alfred/` 和 `config.yaml`，无法修改测试文件或项目外文件
2. **语法门禁**：Python 文件修改后自动 `py_compile`，语法错误则回滚
3. **测试门禁**：修改后自动跑 `pytest`，测试不过则回滚

### 安全约束

- 人类是进化方向的**唯一决策者**：Alfred 不会自行决定"该改什么"
- 每次修改前会向用户展示旧代码和新代码预览，需你确认
- 单轮最多调用一次 `code_patch`，防止连环修改
- 修改失败自动回滚，不留脏状态

### 使用示例

在 chat 中对 Alfred 说："你发现了自己的代码里有什么可以改进的地方吗？"它会先分析，然后用 `code_patch` 生成修改方案请你确认。你也可以主动指定要它改什么。

## 隐私

- 所有记忆、笔记索引、会话历史存储在本地 `data/` 目录
- mem0 telemetry 已关闭（`MEM0_TELEMETRY=false`）
- API key 通过 `.env` + `env_key` 引用，不写入配置文件
- Shell / Python 执行需要用户确认
- 单轮工具调用硬上限 20 次，防止 agent 失控

## 测试

```bash
python -m pytest tests/ -q
```

测试覆盖配置解析、记忆块、Markdown 切分、长期记忆过滤、召回排序、会话历史持久化、skills/rules 扫描、agent 循环等纯逻辑。不依赖真实 LLM 调用或 embedding 下载。

## License

MIT