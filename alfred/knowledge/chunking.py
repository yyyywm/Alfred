"""Markdown 笔记切分：按标题层级切分 + frontmatter 解析。

设计依据：
- 标题层级切分（而非定长切分），每个 chunk 带标题路径前缀，
  保证切片自带上下文（"摘自哪篇笔记的哪一节"）
- frontmatter 解析为可过滤元数据
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")

MAX_CHUNK_CHARS = 1200  # 超长节再按段落二次切分


@dataclass
class Chunk:
    text: str                 # 已带标题路径前缀的正文
    source: str               # 笔记文件路径
    heading_path: str         # 如 "读书笔记/原则/第二章"
    meta: dict = field(default_factory=dict)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, text[m.end():]


def _split_long(text: str, limit: int) -> list[str]:
    """超长文本按段落二次切分。"""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n\n"):
        if buf and len(buf) + len(para) > limit:
            parts.append(buf.strip())
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_markdown(path: Path, root: Path | None = None) -> list[Chunk]:
    """把一篇 Markdown 笔记切成带标题路径的 chunks。"""
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    source = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)

    chunks: list[Chunk] = []
    heading_stack: list[str] = []  # 当前标题路径
    buf: list[str] = []

    def flush():
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        hpath = "/".join(heading_stack)
        prefix = f"[摘自 {source}" + (f" § {hpath}" if hpath else "") + "]\n"
        for part in _split_long(text, MAX_CHUNK_CHARS):
            chunks.append(Chunk(
                text=prefix + part,
                source=source,
                heading_path=hpath,
                meta=meta,
            ))

    for line in body.splitlines():
        m = HEADER_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(m.group(2).strip())
        else:
            buf.append(line)
    flush()
    return chunks
