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
