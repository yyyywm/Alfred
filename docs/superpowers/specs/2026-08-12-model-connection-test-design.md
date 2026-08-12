# 模型连接真实测试设计

日期：2026-08-12

## 背景

当前 `alfred models` 只检查环境变量 key 是否已设置（`alfred/llm.py:58`），并不真正验证模型是否可连接。用户只有在进入 `alfred chat` 后实际发消息，或触发记忆相关路径时，才会发现连接/鉴权/模型名错误。

目标：
1. `alfred models` 支持对单个或全部配置模型做真实连接测试。
2. 不在 `alfred chat` 启动时引入阻塞或耗时的 API 调用，避免启动变慢。
3. 在对话内提供按需连接检查命令 `/status`。

## 设计

### 1. CLI 界面：`alfred models`

修改 `alfred/cli.py` 的 `models` 命令：

```python
def models(
    model_ref: str | None = typer.Argument(None, help="要测试的 provider:model"),
    all_models: bool = typer.Option(False, "--all", help="测试所有配置的模型"),
):
```

行为：
- `alfred models`：保持现有行为，列出 config.yaml 中所有 `provider:model`、类型、key 是否设置。**不调用 API**。
- `alfred models <provider:model>`：对指定模型发一个极轻量的真实请求，输出状态、延迟、错误详情。
- `alfred models --all`：逐个测试所有配置的 `provider:model`。

输出示例：

```text
  ✓  kimi-for-coding:k3  (anthropic)  key 已设置
  ✗  deepseek:deepseek-chat  (openai_compat)  key 未设置

测试 kimi-for-coding:k3 ... ✓  420ms
测试 deepseek:deepseek-chat ... ✗  401 Unauthorized - 请检查 DEEPSEEK_API_KEY
```

### 2. 连接测试实现：`alfred/llm.py`

新增函数：

```python
def test_model_connection(
    config: Config,
    model_ref: str,
    timeout: float = 10.0,
) -> dict:
    """测试单个模型连接。

    返回 {"ok": bool, "latency_ms": float | None, "error": str | None}
    """
```

实现步骤：
1. 调用 `build_model(config, model_ref)` 构建 pydantic-ai model 实例。
2. 创建最小 `Agent(model=model, result_type=str, system_prompt="Reply with only OK.")`。
3. 调用 `agent.run_sync("ping")` 并计时。
4. 捕获所有异常，返回结构化的 `ok / latency_ms / error`。

错误映射（用于输出友好的中文提示）：
- `KeyError` / `ValueError`：配置缺失或环境变量未设置。
- 401 / 403：API key 无效或权限不足。
- 404 / 400：模型名错误或端点路径错误。
- 网络超时 / 连接失败：网络不可达或 base_url 错误。
- 其他异常：兜底为异常字符串。

约束：
- 测试会消耗极少量 token，但它是唯一能同时覆盖 key 有效、网络可达、端点正确、模型名真实存在的方法。
- 调用为同步阻塞，逐模型执行，便于在终端直观展示结果。

### 3. Chat 启动优化与 `/status`

**不**在 `alfred chat` 启动时调用真实 API，避免拖慢启动速度。`build_agent` 已经会做配置解析和 key 存在性检查，若 key 缺失会直接报错退出，因此启动阶段已能捕获基础配置错误。

在对话内新增斜杠命令 `/status`：
- 测试当前 `config.models.chat` 模型连接。
- 可选：同时测试 `config.models.embed` 连接（如果 embed provider 是 `openai_compat`，调用其 `/models` 或做一次小请求）。
- 输出结果到面板，包含当前模型、延迟、状态、错误信息。

示例：

```text
当前模型：kimi-for-coding:k3
  chat   ✓  380ms
  embed  ✓  210ms
```

### 4. 测试策略

- 不调用真实 API。
- 新增 `tests/test_llm.py`：
  - 使用 `monkeypatch` 替换 pydantic-ai `Agent.run_sync`，模拟成功、失败、异常三种情况。
  - 验证 `test_model_connection` 返回结构正确。
  - 验证 `list_models` 默认列表逻辑不变。

## 范围

- 改动文件：`alfred/cli.py`、`alfred/llm.py`、新增 `tests/test_llm.py`。
- 不涉及：记忆层、知识层、向量库、历史记录、配置格式。

## 待实现后的验证

1. `alfred models` 默认行为不变（只列配置，不调用 API）。
2. `alfred models kimi-for-coding:k3` 能真实测试并显示延迟/错误。
3. `alfred models --all` 测试所有配置模型。
4. `alfred chat` 内 `/status` 能检查当前 chat 模型连接。
5. `python -m pytest tests/ -q` 全部通过。
