"""笔记目录（catalog）：笔记库的确定性路由地图。

设计依据：operator-memory 的 Partition Catalog——知识不靠相似度检索入场，
而是维护一份带 Description + Read If 的目录确定性注入 prompt，agent 按任务
匹配后主动精读整篇文档。这补上了纯 top-k RAG 的两个盲区：
- 没检索到的笔记等于不存在（目录让全部笔记可见）
- agent 不知道自己不知道什么（目录让 agent 能按「何时读」自行判断）

与 operator 的差异：operator 的 catalog 由 agent 手写维护；Alfred 的笔记库
是用户的外部资产，条目由 ingest 管线机械生成（标题/首段/标题词），零 LLM、
确定性、可测试。notes_search 保留用于模糊召回，catalog 负责结构化路由。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import Config
from .chunking import HEADER_RE, parse_frontmatter

CATALOG_FILENAME = "notes_catalog.md"

# 目录注入 prompt 有预算约束：条目数封顶，超出部分提示用 notes_search 兜底
MAX_ENTRIES = 100
_SUMMARY_CHARS = 120
_MAX_READ_IF_KEYWORDS = 5


def catalog_path(config: Config) -> Path:
    return config.path(config.paths.vectordb_dir) / CATALOG_FILENAME


def _extract_title(meta: dict, body: str, stem: str) -> str:
    if isinstance(meta.get("title"), str) and meta["title"].strip():
        return meta["title"].strip()
    for line in body.splitlines():
        m = HEADER_RE.match(line.strip())
        if m and len(m.group(1)) == 1:
            return m.group(2).strip()
    return stem


def _extract_summary(body: str) -> str:
    """第一个非标题段落，压缩空白后截断。"""
    for para in re.split(r"\n\s*\n", body):
        text = para.strip()
        if not text or HEADER_RE.match(text):
            continue
        text = re.sub(r"\s+", " ", text)
        return text[:_SUMMARY_CHARS]
    return ""


def _extract_read_if(body: str, stem: str) -> str:
    """从标题（H1-H3）提取路由关键词；没有标题时回退到文件名。"""
    keywords: list[str] = []
    for line in body.splitlines():
        m = HEADER_RE.match(line.strip())
        if m and len(m.group(1)) <= 3:
            kw = m.group(2).strip()
            if kw and kw not in keywords:
                keywords.append(kw)
        if len(keywords) >= _MAX_READ_IF_KEYWORDS:
            break
    if not keywords:
        keywords = [stem]
    return "涉及 " + " / ".join(keywords) + " 时"


def extract_entry(path: Path, root: Path) -> dict:
    """从一篇笔记提取目录条目（纯抽取，不调 LLM）。"""
    rel = str(path.relative_to(root))
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "path": rel,
        "title": _extract_title(meta, body, path.stem),
        "summary": _extract_summary(body),
        "read_if": _extract_read_if(body, path.stem),
    }


def render_catalog(notes_dir: Path, entries: list[dict]) -> str:
    """把条目渲染成注入 prompt 的目录文档。"""
    shown = entries[:MAX_ENTRIES]
    lines = [
        "# 笔记库目录（alfred ingest 自动生成，请勿手改）",
        "",
        f"根目录：{notes_dir}",
        f"共 {len(entries)} 篇笔记。当任务匹配某条的「何时读」时，"
        "用 file_read 打开「根目录 + 相对路径」精读全文；"
        "模糊查找仍用 notes_search。",
        "",
    ]
    for e in shown:
        lines.append(f"- `{e['path']}`")
        lines.append(f"  - 标题：{e['title']}")
        if e["summary"]:
            lines.append(f"  - 摘要：{e['summary']}")
        lines.append(f"  - 何时读：{e['read_if']}")
    if len(entries) > MAX_ENTRIES:
        lines.append("")
        lines.append(
            f"（其余 {len(entries) - MAX_ENTRIES} 篇未列出，用 notes_search 检索）"
        )
    return "\n".join(lines)


def rebuild_catalog(config: Config, notes_dir: Path, md_files: list[Path]) -> None:
    """ingest 结束后重建目录。失败不阻塞索引主流程（目录是增强，不是命脉）。"""
    try:
        entries = [extract_entry(f, notes_dir) for f in md_files]
        out = catalog_path(config)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_catalog(notes_dir, entries), encoding="utf-8")
    except Exception as exc:
        logging.getLogger(__name__).warning("笔记目录重建失败：%s", exc)


def load_catalog(config: Config) -> str:
    """chat 启动时一次性读取目录文本；不存在返回空串（不注入）。"""
    p = catalog_path(config)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""
