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

### 使用 conda（推荐，含 web 版 conda 环境）

```bash
conda env create -f environment.yml
conda activate alfred
cp .env.example .env   # 填入你的 API key
```

### 使用 pip（不使用 conda 时）

```bash
pip install -e ".[dev]"
cp .env.example .env   # 填入你的 API key
```

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
