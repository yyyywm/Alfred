"""Markdown 切分测试。"""

from alfred.knowledge.chunking import chunk_markdown, parse_frontmatter

NOTE = """---
title: 读书笔记
tags: [reading, thinking]
---

# 第一章 原则

先把事情做对，再把事情做好。

## 1.1 做正确的事

方向比努力重要。选择不做什么，比选择做什么更关键。

# 第二章 方法

小步快跑，快速验证。
"""


def test_frontmatter_parsing():
    meta, body = parse_frontmatter(NOTE)
    assert meta["title"] == "读书笔记"
    assert "第一章" in body


def test_heading_chunks(tmp_path):
    f = tmp_path / "note.md"
    f.write_text(NOTE, encoding="utf-8")
    chunks = chunk_markdown(f, root=tmp_path)
    assert len(chunks) >= 3
    # 每个 chunk 都带出处前缀
    assert all("note.md" in c.text for c in chunks)
    # 标题路径正确
    paths = [c.heading_path for c in chunks]
    assert "第一章 原则" in paths
    assert any("1.1" in p for p in paths)
    # frontmatter 进了元数据，不在正文
    assert all("title:" not in c.text for c in chunks)
    assert chunks[0].meta.get("title") == "读书笔记"


def test_long_section_split(tmp_path):
    long_text = "# 标题\n\n" + "\n\n".join(f"段落{i} " + "内容" * 200 for i in range(10))
    f = tmp_path / "long.md"
    f.write_text(long_text, encoding="utf-8")
    chunks = chunk_markdown(f)
    assert len(chunks) > 1
    assert all(len(c.text) < 1400 for c in chunks)  # 1200 上限 + 前缀
