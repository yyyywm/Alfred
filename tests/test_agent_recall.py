"""memory_search 的召回记录累加语义测试（不调用真实 LLM/向量库）。

回归：memory_search 里 `deps.last_recalled = [...]` 是重新绑定，一轮内
第二次召回会覆盖第一次——`/why` 只能显示最后一次召回的依据，前面几次的
记忆引用凭空消失。改成 extend 后一轮内的全部召回都被记录。
"""

from pydantic_ai import RunContext

from alfred import agent as agent_mod
from alfred.config import Config, ProviderConfig
from alfred.memory import recall as recall_mod


def _tool_function(agent, name: str):
    """取注册工具的原始函数（pydantic_ai 存的是注入 ctx 前的包装）。"""
    tool = agent.toolsets[0].tools[name]
    fn = tool.function
    return getattr(fn, "__wrapped__", fn)


def _agent_in(tmp_path, monkeypatch):
    """用假 provider 构建 agent，召回打桩成固定结果。"""
    cfg = Config(
        providers={
            "p": ProviderConfig(
                type="openai_compat", base_url="https://example.com/v1",
                env_key="", models=["m"],
            )
        },
        models={"chat": "p:m", "memory_write": "p:m",
                "embed": {"provider": "local", "name": "BAAI/bge-large-zh-v1.5"}},
        memory={"dir": str(tmp_path / "mem")},
        paths={"history_dir": str(tmp_path / "hist"), "vectordb_dir": str(tmp_path / "vdb")},
    )
    calls = []

    def fake_recall(config, query, user_id=None):
        calls.append(query)
        if len(calls) == 1:
            return [{"memory": "用户喜欢咖啡"}, {"memory": "用户在深圳"}]
        return [{"memory": "用户养了一只猫"}]

    monkeypatch.setattr(recall_mod, "recall", fake_recall)
    monkeypatch.setattr(recall_mod, "render_for_prompt", lambda ms: "|".join(
        m.get("memory", str(m)) for m in ms))
    return cfg, agent_mod.build_agent(cfg)


def test_memory_search_accumulates_recalls_within_a_turn(tmp_path, monkeypatch):
    """一轮内多次 memory_search：全部召回都留在 last_recalled 里。"""
    cfg, agent = _agent_in(tmp_path, monkeypatch)
    search = _tool_function(agent, "memory_search")

    deps = agent_mod.AlfredDeps(config=cfg, blocks=None, last_recalled=[])
    ctx = RunContext(deps=deps, model=None, usage=None)

    assert search(ctx, "喜好") == "用户喜欢咖啡|用户在深圳"
    assert search(ctx, "宠物") == "用户养了一只猫"

    # 旧实现这里是 ['用户养了一只猫']——第一次召回被覆盖，/why 丢失依据
    assert deps.last_recalled == ["用户喜欢咖啡", "用户在深圳", "用户养了一只猫"]


def test_recall_list_not_shared_between_deps(tmp_path, monkeypatch):
    """新 deps 的列表必须独立：否则上轮的召回会串进这一轮的 /why。

    chat_turn_stream 里 turn_deps 用 last_recalled=[] 新建，而不是复用
    deps.last_recalled（旧写法）。这里用两个 deps 模拟两轮，验证互不串扰。
    """
    cfg, agent = _agent_in(tmp_path, monkeypatch)
    search = _tool_function(agent, "memory_search")

    turn1 = agent_mod.AlfredDeps(config=cfg, blocks=None, last_recalled=[])
    search(RunContext(deps=turn1, model=None, usage=None), "喜好")
    assert turn1.last_recalled == ["用户喜欢咖啡", "用户在深圳"]

    turn2 = agent_mod.AlfredDeps(config=cfg, blocks=None, last_recalled=[])
    search(RunContext(deps=turn2, model=None, usage=None), "宠物")
    # 第二轮只含自己的召回；旧写法下会带着第一轮的残留
    assert turn2.last_recalled == ["用户养了一只猫"]
    assert turn1.last_recalled == ["用户喜欢咖啡", "用户在深圳"]
