"""笔记目录（catalog）测试：纯逻辑，不调 LLM/embedding。

设计依据：operator-memory 的 Partition Catalog——确定性路由地图，
条目带 Description + Read If，agent 按任务匹配后主动精读。
Alfred 的版本由 ingest 管线机械生成（零 LLM）。
"""

from alfred.config import Config
from alfred.knowledge import catalog, ingest


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_extract_entry_title_priority(tmp_path):
    """标题优先级：frontmatter title > 首个 H1 > 文件名。"""
    root = tmp_path / "notes"
    _write(root / "a.md", "---\ntitle: 原则读书笔记\n---\n\n# 正文标题\n\n内容。")
    _write(root / "b.md", "# 只有正文标题\n\n内容。")
    _write(root / "c.md", "没有标题的正文。")

    e = catalog.extract_entry(root / "a.md", root)
    assert e["title"] == "原则读书笔记"
    e = catalog.extract_entry(root / "b.md", root)
    assert e["title"] == "只有正文标题"
    e = catalog.extract_entry(root / "c.md", root)
    assert e["title"] == "c"


def test_extract_entry_summary_and_read_if(tmp_path):
    """摘要取第一个非标题段落（跳过 frontmatter）；何时读取 H1-H3 标题词。"""
    root = tmp_path / "notes"
    _write(
        root / "d.md",
        "---\ntags: [x]\n---\n\n# 决策方法论\n\n"
        "这是关于如何做决策的笔记。\n\n## 双清单法\n\n细节。\n\n## 逆向思维\n\n细节。",
    )
    e = catalog.extract_entry(root / "d.md", root)
    assert e["summary"] == "这是关于如何做决策的笔记。"
    assert "决策方法论" in e["read_if"]
    assert "双清单法" in e["read_if"]

    # 无标题时 read_if 回退到文件名
    _write(root / "plain-note.md", "随手记的一段东西。")
    e = catalog.extract_entry(root / "plain-note.md", root)
    assert e["read_if"] == "涉及 plain-note 时"


def test_extract_entry_summary_skips_leading_heading_only_doc(tmp_path):
    """整篇只有标题没有正文段落时，摘要为空。"""
    root = tmp_path / "notes"
    _write(root / "e.md", "# 只有标题\n\n## 子标题\n")
    e = catalog.extract_entry(root / "e.md", root)
    assert e["summary"] == ""


def test_render_catalog_structure_and_overflow(tmp_path):
    """目录文档包含根目录与用法说明；超过 MAX_ENTRIES 时提示用 notes_search 兜底。"""
    entries = [
        {"path": f"n{i}.md", "title": f"笔记{i}",
         "summary": "摘要" if i % 2 == 0 else "", "read_if": "涉及 x 时"}
        for i in range(catalog.MAX_ENTRIES + 10)
    ]
    text = catalog.render_catalog(tmp_path / "notes", entries)

    assert f"根目录：{tmp_path / 'notes'}" in text
    assert "file_read" in text and "notes_search" in text
    assert text.count("- 标题：") == catalog.MAX_ENTRIES
    assert f"其余 10 篇未列出" in text
    # 空摘要条目不渲染摘要行
    first_empty = text.split("- `n1.md`")[1].split("- `n2.md`")[0]
    assert "摘要" not in first_empty


def test_rebuild_and_load_roundtrip(tmp_path):
    """rebuild 写入 vectordb 目录，load 读回；文件不存在时 load 返回空串。"""
    cfg = Config(paths={"vectordb_dir": str(tmp_path / "vdb")})
    assert catalog.load_catalog(cfg) == ""

    root = tmp_path / "notes"
    _write(root / "x.md", "# 测试笔记\n\n正文内容。")
    catalog.rebuild_catalog(cfg, root, [root / "x.md"])

    text = catalog.load_catalog(cfg)
    assert "测试笔记" in text
    assert "x.md" in text


def test_ingest_rebuilds_catalog(tmp_path, monkeypatch):
    """ingest 管线结束后自动重建目录（embedding/存储打桩，不碰真实模型）。"""
    root = tmp_path / "notes"
    _write(root / "n1.md", "# 笔记一\n\n第一篇的内容。")
    _write(root / "sub" / "n2.md", "# 笔记二\n\n第二篇的内容。")

    cfg = Config(paths={"vectordb_dir": str(tmp_path / "vdb")})
    monkeypatch.setattr(
        ingest, "embed_texts", lambda config, texts: [[0.1, 0.2]] * len(texts),
    )
    monkeypatch.setattr(
        ingest.store, "upsert_chunks", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        ingest.store, "delete_by_source", lambda *a, **k: None,
    )

    stats = ingest.ingest(cfg, root)
    assert stats["added"] == 2

    text = catalog.load_catalog(cfg)
    assert "笔记一" in text and "笔记二" in text
    assert "sub" in text  # 相对路径保留子目录结构


def test_ingest_catalog_failure_does_not_break_pipeline(tmp_path, monkeypatch):
    """目录重建抛异常不影响索引主流程（目录是增强，不是命脉）。"""
    root = tmp_path / "notes"
    _write(root / "n1.md", "# 笔记一\n\n内容。")

    cfg = Config(paths={"vectordb_dir": str(tmp_path / "vdb")})
    monkeypatch.setattr(
        ingest, "embed_texts", lambda config, texts: [[0.1]] * len(texts),
    )
    monkeypatch.setattr(ingest.store, "upsert_chunks", lambda *a, **k: None)
    monkeypatch.setattr(ingest.store, "delete_by_source", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("catalog exploded")

    # rebuild_catalog 内部已 try/except；这里再验证极端情况（extract 崩溃）
    monkeypatch.setattr(ingest.catalog, "extract_entry", boom)

    stats = ingest.ingest(cfg, root)
    assert stats["added"] == 1  # 主流程不受影响
