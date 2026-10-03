"""联网工具核心逻辑：web_search（搜索）+ web_fetch（网页正文抽取）。

设计依据：
- 对标 Kimi Code 宿主侧 FetchURL/WebSearch：工具即动作空间（CodeAct），
  模型只发工具调用，网络访问由宿主完成。
- 搜索双后端：bing（免 key，爬 HTML）/ tavily（结构化 API），
  由 config.web.search_provider 切换。
- 纯函数、不依赖 agent 内核；HTTP 客户端可注入（MockTransport 便于测试）。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote_plus

import httpx

from .config import Config

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 单次抓取最多下载的字节数，防大页面撑爆内存
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024


class WebError(Exception):
    """联网操作失败的统一异常，消息面向用户可读。"""


def _default_client(config: Config) -> httpx.Client:
    return httpx.Client(
        timeout=config.web.timeout_s,
        follow_redirects=True,
        headers={"User-Agent": _UA},
    )


# ── 搜索 ─────────────────────────────────────────────────────────────


def search_web(
    config: Config,
    query: str,
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    """搜索网页，返回 [{title, url, snippet}]。失败抛 WebError。"""
    limit = limit or config.web.search_max_results
    provider = config.web.search_provider
    if provider == "tavily":
        key = config.web.search_api_key()
        if key:
            return _search_tavily(config, query, limit, key, client)
        # 缺 key 不报错，回退 bing，由工具层告知用户
    return _search_bing(config, query, limit, client)


def _search_tavily(
    config: Config,
    query: str,
    limit: int,
    api_key: str,
    client: httpx.Client | None,
) -> list[dict]:
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "include_answer": False,
    }
    try:
        if client is not None:
            resp = client.post("https://api.tavily.com/search", json=payload)
        else:
            with _default_client(config) as c:
                resp = c.post("https://api.tavily.com/search", json=payload)
    except httpx.HTTPError as e:
        raise WebError(f"Tavily 搜索请求失败：{e}") from e
    if resp.status_code != 200:
        raise WebError(f"Tavily 搜索返回 HTTP {resp.status_code}（检查 API key 与额度）。")
    try:
        data = resp.json()
    except ValueError as e:
        raise WebError("Tavily 搜索返回了无法解析的响应。") from e
    results = []
    for item in data.get("results", [])[:limit]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            }
        )
    if not results:
        raise WebError(f"搜索「{query}」没有找到结果。")
    return results


class _BingResultsParser(HTMLParser):
    """解析 Bing 搜索结果页：li.b_algo 内的 h2>a（标题+链接）与首个 p（摘要）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._in_algo = False
        self._in_h2 = False
        self._in_a = False
        self._in_p = False
        self._cur: dict | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "li" and "b_algo" in classes:
            self._in_algo = True
            self._cur = {"title": "", "url": "", "snippet": ""}
            self._title_parts = []
            self._snippet_parts = []
        elif self._in_algo and tag == "h2":
            self._in_h2 = True
        elif self._in_h2 and tag == "a" and not self._cur["url"]:
            self._in_a = True
            self._cur["url"] = attr.get("href") or ""
        elif self._in_algo and tag == "p" and not self._snippet_parts:
            self._in_p = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._in_algo:
            self._cur["title"] = " ".join("".join(self._title_parts).split())
            self._cur["snippet"] = " ".join("".join(self._snippet_parts).split())
            if self._cur["url"]:
                self.results.append(self._cur)
            self._in_algo = False
            self._in_h2 = False
            self._in_a = False
            self._in_p = False
            self._cur = None
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "a":
            self._in_a = False
        elif tag == "p":
            self._in_p = False

    def handle_data(self, data: str) -> None:
        if self._in_a and self._cur is not None:
            self._title_parts.append(data)
        elif self._in_p and self._cur is not None:
            self._snippet_parts.append(data)


def _search_bing(
    config: Config,
    query: str,
    limit: int,
    client: httpx.Client | None,
) -> list[dict]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={limit + 5}"
    try:
        if client is not None:
            resp = client.get(url)
        else:
            with _default_client(config) as c:
                resp = c.get(url)
    except httpx.HTTPError as e:
        raise WebError(f"搜索请求失败：{e}") from e
    if resp.status_code != 200:
        raise WebError(f"搜索返回 HTTP {resp.status_code}（可能触发了 Bing 反爬，稍后再试）。")
    parser = _BingResultsParser()
    parser.feed(resp.text)
    results = parser.results[:limit]
    if not results:
        raise WebError(f"搜索「{query}」没有解析到结果（Bing 页面结构可能已变更）。")
    return results


# ── 网页抓取 ─────────────────────────────────────────────────────────


_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "tr", "table", "blockquote", "pre",
}


class _TextExtractor(HTMLParser):
    """抽取 HTML 可见文本：跳过脚本/样式，块级标签换行，压缩空白。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [" ".join(line.split()) for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def fetch_webpage(
    config: Config,
    url: str,
    client: httpx.Client | None = None,
) -> str:
    """抓取网页并抽取正文文本，截断到 config.web.fetch_max_chars。失败抛 WebError。"""
    if not re.match(r"^https?://", url):
        raise WebError(f"URL 应以 http:// 或 https:// 开头：{url}")

    try:
        if client is not None:
            resp = client.get(url)
            content_bytes = resp.content[: _MAX_DOWNLOAD_BYTES + 1]
        else:
            with _default_client(config) as c, c.stream("GET", url) as resp:
                chunks: list[bytes] = []
                downloaded = 0
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if downloaded > _MAX_DOWNLOAD_BYTES:
                        break
                content_bytes = b"".join(chunks)
    except httpx.HTTPError as e:
        raise WebError(f"网页抓取失败：{e}") from e

    if resp.status_code != 200:
        raise WebError(f"网页返回 HTTP {resp.status_code}：{url}")

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type:
        kind = content_type.split(";")[0].strip() or "未知类型"
        raise WebError(
            f"该 URL 不是 HTML 网页（Content-Type: {kind}），无法抽取正文。"
        )

    html = content_bytes.decode(resp.encoding or "utf-8", errors="replace")
    extractor = _TextExtractor()
    extractor.feed(html)
    text = extractor.text()
    if not text:
        raise WebError(f"页面没有可抽取的正文文本（可能是动态渲染页面）：{url}")

    max_chars = config.web.fetch_max_chars
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[内容已截断，原始页面: {url}]"
    return text
