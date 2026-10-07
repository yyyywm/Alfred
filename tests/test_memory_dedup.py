"""事实型记忆写入前去重的测试（不调用真实 LLM/embedding/mem0）。

设计依据：operator-memory 对 replay 式记忆的批判——"一个事实一份记录"，
过时的/重复的记录不能并排累积。mem0 内部的 ADD/UPDATE/DELETE 推断处理
语义级冲突，add_fact 在其之前加一道确定性去重门禁，挡住 consolidate
反复运行产生的近重复记录。
"""

from alfred.config import Config
from alfred.memory import longterm


def setup_function():
    longterm.reset_clients()


def teardown_function():
    longterm.reset_clients()


class FakeClient:
    """内存版 MemoryClient：search 返回预设邻居，add 记录写入。"""

    def __init__(self, neighbors=None):
        self.neighbors = neighbors or []
        self.added: list[str] = []

    def add(self, messages, *, user_id="owner", metadata=None):
        self.added.append(messages[0]["content"])

    def search(self, query, limit=10, *, user_id="owner"):
        return self.neighbors[:limit]

    def list_all(self, limit=100, *, user_id="owner"):
        return []

    def delete(self, memory_id, *, user_id=None):
        return True


def _use_client(monkeypatch, client) -> Config:
    cfg = Config(memory={"default_user_id": "owner"})
    monkeypatch.setattr(longterm, "get_client", lambda *a, **k: client)
    return cfg


def test_jaccard_signature_handles_chinese():
    """中文按字取集合：近重复高分，无关文本低分。"""
    a = longterm._text_signature("用户喜欢喝咖啡")
    b = longterm._text_signature("用户喜欢喝咖啡")
    assert longterm._jaccard(a, b) == 1.0

    c = longterm._text_signature("用户每天跑步五公里")
    assert longterm._jaccard(a, c) < 0.5

    # 空集合不报错
    assert longterm._jaccard(set(), set()) == 0.0


def test_is_near_duplicate_threshold():
    assert longterm.is_near_duplicate("用户喜欢喝咖啡", "用户喜欢喝咖啡")
    # 差一个字：7/8 = 0.875 ≥ 0.85，视为同一事实的重复表述
    assert longterm.is_near_duplicate("用户喜欢喝冰咖啡", "用户喜欢喝咖啡")
    # 真不同的事实不能误杀：每周三 vs 每周四 = 0.8 < 0.85
    assert not longterm.is_near_duplicate("用户每周三打羽毛球", "用户每周四打羽毛球")
    assert not longterm.is_near_duplicate("用户喜欢喝咖啡", "用户在健身环大冒险")


def test_add_fact_skips_near_duplicate(monkeypatch):
    """已存在近重复记忆时跳过写入，返回 existing 供调用方记录。"""
    client = FakeClient(neighbors=[{"memory": "用户喜欢喝冰咖啡"}])
    cfg = _use_client(monkeypatch, client)

    result = longterm.add_fact(cfg, "用户喜欢喝咖啡")
    assert result["status"] == "duplicate"
    assert result["existing"] == "用户喜欢喝冰咖啡"
    assert client.added == []  # 没有写入


def test_add_fact_vector_score_catches_rewritten_duplicates(monkeypatch):
    """mem0 抽取会把中文事实改写成英文存储，文本 Jaccard 失效；
    向量分 ≥ 0.85 兜底判重（真实链路校准：同义复述 ≥ 0.91）。"""
    client = FakeClient(neighbors=[{
        "memory": "User enjoys pour-over coffee and prefers light roast",
        "score": 0.91,
    }])
    cfg = _use_client(monkeypatch, client)

    result = longterm.add_fact(cfg, "用户喜欢喝手冲咖啡，偏好浅烘豆")
    assert result["status"] == "duplicate"
    assert client.added == []


def test_add_fact_vector_score_below_threshold_keeps_distinct_facts(monkeypatch):
    """向量分 0.73 的近义异事实（喜欢美式 vs 喜欢手冲）不能误杀。"""
    client = FakeClient(neighbors=[{
        "memory": "用户喜欢喝手冲咖啡",
        "score": 0.73,
    }])
    cfg = _use_client(monkeypatch, client)

    result = longterm.add_fact(cfg, "用户喜欢喝美式咖啡")
    assert result["status"] == "added"
    assert client.added == ["用户喜欢喝美式咖啡"]


def test_add_fact_missing_score_falls_back_to_text(monkeypatch):
    """协议不保证 score 字段：缺失时只靠文本信号，低相似文本正常写入。"""
    client = FakeClient(neighbors=[{"memory": "User enjoys coffee"}])  # 无 score
    cfg = _use_client(monkeypatch, client)

    result = longterm.add_fact(cfg, "用户喜欢喝咖啡")
    assert result["status"] == "added"


def test_add_fact_writes_when_no_duplicate(monkeypatch):
    """邻居相似度不足时正常写入。"""
    client = FakeClient(neighbors=[{"memory": "用户在深圳工作"}])
    cfg = _use_client(monkeypatch, client)

    result = longterm.add_fact(cfg, "用户喜欢喝咖啡")
    assert result["status"] == "added"
    assert client.added == ["用户喜欢喝咖啡"]


def test_add_fact_search_failure_does_not_block_write(monkeypatch):
    """检索抛异常时照常写入（mem0 内部推断兜底），不让去重门禁变成写入阻塞。"""

    class BrokenSearchClient(FakeClient):
        def search(self, query, limit=10, *, user_id="owner"):
            raise RuntimeError("vector store down")

    client = BrokenSearchClient()
    cfg = _use_client(monkeypatch, client)

    result = longterm.add_fact(cfg, "用户喜欢喝咖啡")
    assert result["status"] == "added"
    assert client.added == ["用户喜欢喝咖啡"]


def test_add_fact_offline_when_no_client(monkeypatch):
    """记忆层离线时不写入、不抛异常，状态明确为 offline。"""
    cfg = Config(memory={"default_user_id": "owner"})
    monkeypatch.setattr(longterm, "get_client", lambda *a, **k: None)

    assert longterm.add_fact(cfg, "任何事实") == {"status": "offline"}


def test_consolidate_unattended_dedupes_memory_entries(monkeypatch, tmp_path):
    """apply_unattended 走 add_fact：重复条目标记跳过而非重复写入。"""
    from alfred.memory import consolidate

    client = FakeClient(neighbors=[{"memory": "用户喜欢喝冰咖啡"}])
    cfg = Config(
        memory={"dir": str(tmp_path / "mem"), "default_user_id": "owner"},
        paths={"history_dir": str(tmp_path / "hist"),
               "vectordb_dir": str(tmp_path / "vdb")},
    )
    monkeypatch.setattr(longterm, "get_client", lambda *a, **k: client)

    drafts = {"memory_entries": ["用户喜欢喝咖啡", "用户每周三打羽毛球"]}
    applied = consolidate.apply_unattended(cfg, drafts)

    assert client.added == ["用户每周三打羽毛球"]  # 重复的那条没写
    assert any("已存在，跳过" in a for a in applied)
    assert any(a == "记忆条目：用户每周三打羽毛球" for a in applied)
