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
