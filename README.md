# Alfred —— 你的私人管家 AI

了解你的一切，记得你说过的每件事，读你读过的书，陪你做决定。

Alfred 是一个以**长期记忆**和**个人知识库**为核心的私人智能体：

- **认识你**：跨会话记住你的经历、偏好、思维方式，越用越懂你
- **读你的笔记**：索引个人笔记库，回答问题时引用出处
- **可以喂养**：喂给它书籍文章，提炼成思维框架，成为它思考的工具
- **会成长**：定期复盘对话，沉淀记忆、规则和技能（你确认后才入库）
- **模型无关**：任何 OpenAI / Anthropic 兼容的 API 都能接，换模型不改代码

## 安装

环境要求：Python 3.11+

Alfred 的依赖声明在 `pyproject.toml` 中（包括 `pydantic-ai`、`mem0ai`、`lancedb`、`sentence-transformers`、`prompt-toolkit` 等）。`environment.yml` 只做一件事：创建 conda 环境后，通过 `pip install -e ".[dev]"` 自动安装这些依赖。

### 使用 conda（推荐）

```bash
conda env create -f environment.yml
conda activate alfred
cp .env.example .env   # 填入至少一个 LLM API key
```

验证安装：

```bash
alfred models    # 应该列出配置的模型，而不是报 ModuleNotFoundError
```

### 使用 pip（不使用 conda 时）

```bash
pip install -e ".[dev]"
cp .env.example .env   # 填入至少一个 LLM API key
```

验证安装：

```bash
alfred models
```

### 常见安装问题

- **报错 `ModuleNotFoundError: No module named 'alfred'`**：说明当前 Python 环境没有安装 alfred 包。在项目根目录执行 `pip install -e ".[dev]"` 即可。如果你之前在其他目录安装过，先运行 `pip uninstall alfred`，再到本项目根目录重新安装。
- **不知道该激活哪个环境**：`conda env list` 查看环境；如果列表里没有 `alfred`，先执行 `conda env create -f environment.yml`。
- **`/memory` 或 `alfred memory list` 长期为空**：可能是 mem0 本地向量库初始化失败。检查 `data/vectordb/qdrant_mem0/.lock` 是否存在，存在则删除后重启； Alfred 已支持自动清理该锁文件，若仍失败请查看 `data/logs/alfred.log`。

## 快速开始

```bash
alfred models            # 检查配置的模型与 key 可用性
alfred chat              # 开始对话
alfred ingest ~/notes    # 索引笔记目录（首次需下载 embedding 模型 ~600MB）
alfred feed book.md      # 喂养一本书，提炼思维框架
alfred consolidate       # 睡眠整理：复盘近期对话，确认后沉淀记忆
alfred memory list       # 查看长期记忆
alfred skills            # 查看技能与规则
```

chat 交互说明：

- 输入支持行编辑：方向键移动光标、Backspace/Delete、Home/End、上下翻阅历史
- 发送后显示 `助手正在思考...` 状态提示，收到回复后自动切换为 `助手： ` 前缀
- 你的输入和 Alfred 的回复之间有空行分隔，回复前有 `助手： ` 前缀
- 工具调用会单独成行显示（如 `🔧 memory_search ✓`）
- 按 `Ctrl-C` 可中断当前回复，不会退出对话
- 对话过程会记录日志到 `data/logs/alfred.log`，便于排查问题

chat 启动选项：

```bash
alfred chat                # 正常对话
alfred chat --debug        # 启用调试日志，同时输出到控制台
alfred chat -s <session>   # 恢复指定会话
```

chat 内斜杠命令：

| 命令 | 作用 |
|---|---|
| `/new` | 开启新会话 |
| `/model provider:model` | 切换闲聊模型 |
| `/remember <内容>` | 显式教学，写入用户画像 |
| `/memory` | 查看长期记忆 |
| `/why` | 查看上一轮回答依据了哪些记忆 |
| `/sessions` | 列出历史会话（带序号） |
| `/load <序号或id>` | 加载历史会话，继续之前的上下文 |
| `/delete <序号或id>` | 删除会话记录（需确认） |
| `/status` | 检查当前模型与 embedding 连接状态 |
| `/exit` | 退出 |

## 配置（config.yaml）

```yaml
providers:
  kimi-for-coding:           # 任何 Anthropic / OpenAI 兼容端点都能接
    type: anthropic
    base_url: https://api.kimi.com/coding
    env_key: KIMI_API_KEY    # key 从 .env 读取，不写在这里
    models: [k3]

models:
  chat: kimi-for-coding:k3          # 闲聊路径：对话中可用 /model 自由切
  memory_write: kimi-for-coding:k3  # 记忆写入路径：固定强模型，质量敏感
  embed:
    name: Qwen/Qwen3-Embedding-0.6B # 本地 embedding：选定后不要换（换=重建索引）
```

## 扩展能力

- **加技能**：在 `skills/<名字>/SKILL.md` 写 frontmatter（name/description）+ 流程正文，
  Alfred 在任务匹配时自动激活
- **加规则**：在 `rules/*.md` 写 frontmatter（`alwaysApply` 常驻 / `description`
  按需召回 / `globs` 按文件匹配）
- **自我成长**：日常对话自动沉淀长期记忆；定期 `alfred consolidate` 复盘产出
  记忆/规则/技能草稿，确认后入库（git 版本化，可回滚）

## 隐私

所有记忆、笔记索引、会话历史全部存储在本地 `data/` 目录，唯一的对外流量是你配置的
LLM API。记忆完全透明可审计：`alfred memory list/delete`、对话内 `/why`。

## 测试

```bash
python -m pytest tests/ -q
```

## License

MIT
