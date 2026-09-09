"""会话标题：meta 存储、优先级、回退预览、标题匹配解析（纯逻辑，不碰真实 LLM）。"""

from alfred.config import Config
from alfred.history import Session, delete_session, get_title, set_title


def _cfg(tmp_path):
    return Config(
        memory={"dir": str(tmp_path / "mem")},
        paths={"history_dir": str(tmp_path / "hist")},
    )


def test_set_and_get_title(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("你好")
    assert get_title(cfg, s.id) is None
    set_title(cfg, s.id, "闲聊", auto=False)
    assert get_title(cfg, s.id) == "闲聊"


def test_delete_session_cleans_meta(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("要删除")
    set_title(cfg, s.id, "临时", auto=False)
    assert delete_session(cfg, s.id) is True
    assert get_title(cfg, s.id) is None


def test_corrupt_meta_degrades_to_none(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    meta_file = cfg.path(cfg.paths.history_dir) / "sessions_meta.json"
    meta_file.write_text("{not json", encoding="utf-8")
    assert get_title(cfg, s.id) is None


def test_list_sessions_merges_title(tmp_path):
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("讨论记账软件")
    set_title(cfg, s.id, "记账软件选型", auto=True)
    (info,) = list_sessions(cfg)
    assert info.id == s.id
    assert info.title == "记账软件选型"
    assert info.title_auto is True
    assert info.msg_count == 1


def test_sessions_meta_file_not_listed(tmp_path):
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    set_title(cfg, s.id, "标题", auto=True)
    ids = {i.id for i in list_sessions(cfg)}
    assert ids == {s.id}


def test_corrupt_meta_list_sessions_title_none(tmp_path):
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    meta_file = cfg.path(cfg.paths.history_dir) / "sessions_meta.json"
    meta_file.write_text("{not json", encoding="utf-8")
    (info,) = list_sessions(cfg)
    assert info.title is None


def test_clean_title():
    from alfred.titles import _clean_title
    assert _clean_title("记账软件选型") == "记账软件选型"
    assert _clean_title('  "记账软件选型"。\n多余行') == "记账软件选型"
    assert _clean_title("「读书计划」") == "读书计划"
    assert _clean_title("") is None
    assert _clean_title("   \n  ") is None
    assert len(_clean_title("一" * 50)) == 30  # 超长截断


def test_auto_title_never_overrides(tmp_path):
    from alfred.titles import write_auto_title
    cfg = _cfg(tmp_path)
    # 手动标题优先：自动概括不得覆盖
    s = Session(cfg)
    s.add_user("内容")
    set_title(cfg, s.id, "手动标题", auto=False)
    assert write_auto_title(cfg, s.id, "自动标题") is False
    assert get_title(cfg, s.id) == "手动标题"
    # 无标题时可以写入
    s2 = Session(cfg)
    s2.add_user("另一个会话")
    assert write_auto_title(cfg, s2.id, "自动标题") is True
    assert get_title(cfg, s2.id) == "自动标题"
    # 已有自动标题也不再重复覆盖
    assert write_auto_title(cfg, s2.id, "又来一个") is False
    assert get_title(cfg, s2.id) == "自动标题"


def test_malformed_meta_entry_degrades(tmp_path):
    """meta 条目不是 dict（手改损坏）时，get_title/list_sessions 不崩。"""
    import json
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    meta_file = cfg.path(cfg.paths.history_dir) / "sessions_meta.json"
    meta_file.write_text(json.dumps({s.id: "oops-not-a-dict"}), encoding="utf-8")
    assert get_title(cfg, s.id) is None
    (info,) = list_sessions(cfg)
    assert info.title is None
    assert info.title_auto is True


def test_session_preview_fallback(tmp_path):
    from alfred.history import session_preview
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("帮我分析一下最近很火的 AI 编程工具")
    assert session_preview(cfg, s.id, max_chars=10) == "帮我分析一下最近很火…"
    assert session_preview(cfg, s.id, max_chars=100) == "帮我分析一下最近很火的 AI 编程工具"
    # 无用户消息 / 会话不存在 → None
    s2 = Session(cfg)
    assert session_preview(cfg, s2.id) is None
    assert session_preview(cfg, "nonexistent") is None


def test_resolve_session_ref_by_title(tmp_path):
    from alfred.cli import _resolve_session_ref
    from alfred.history import list_sessions
    cfg = _cfg(tmp_path)
    s1 = Session(cfg)
    s1.add_user("a")
    set_title(cfg, s1.id, "记账讨论", auto=False)
    s2 = Session(cfg)
    s2.add_user("b")
    set_title(cfg, s2.id, "读书计划", auto=False)
    s3 = Session(cfg)
    s3.add_user("c")
    set_title(cfg, s3.id, "健身计划", auto=False)

    listed = list_sessions(cfg)
    # 序号
    assert _resolve_session_ref(cfg, "1", listed) == listed[0].id
    # id 前缀
    assert _resolve_session_ref(cfg, s1.id[:6], listed) == s1.id
    # 标题唯一匹配（大小写不敏感）
    assert _resolve_session_ref(cfg, "记账", listed) == s1.id
    # 标题多匹配 → None（CLI 侧会列出候选）
    assert _resolve_session_ref(cfg, "计划", listed) is None
    # 都不匹配 → None
    assert _resolve_session_ref(cfg, "不存在", listed) is None


def test_set_title_if_absent_atomic(tmp_path):
    """原子 check-and-set：已有标题（含自动标题）时拒绝写入。"""
    from alfred.history import set_title_if_absent
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("内容")
    assert set_title_if_absent(cfg, s.id, "自动标题") is True
    assert get_title(cfg, s.id) == "自动标题"
    assert set_title_if_absent(cfg, s.id, "又来一个") is False
    assert get_title(cfg, s.id) == "自动标题"
