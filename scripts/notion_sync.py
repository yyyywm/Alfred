# -*- coding: utf-8 -*-
"""Notion 增量同步：ntn CLI → 本地 markdown（供 alfred ingest 索引）

用法: python notion_sync.py [--full]
  默认增量（对比 last_edited_time）；--full 强制全量重拉。
输出: data/notion_export/<标题>-<id前8位>.md
状态: data/notion_export/.sync_state.json
"""
import json, os, re, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Alfred 根目录
OUT_DIR = os.path.join(BASE, "data", "notion_export")
STATE_FILE = os.path.join(OUT_DIR, ".sync_state.json")
os.makedirs(OUT_DIR, exist_ok=True)

def ntn_api(path, body=None):
    """调 ntn api，返回解析后的 JSON。"""
    cmd = ["ntn", "api", path]
    if body is not None:
        cmd += ["--data", json.dumps(body)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ntn 失败: {path}\n{r.stderr[:500]}")
    return json.loads(r.stdout)

def list_all_pages():
    """POST /v1/search 分页拿全部 page（含标题和 last_edited_time）。"""
    pages, cursor = [], None
    while True:
        body = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = ntn_api("v1/search", body)
        for it in resp.get("results", []):
            if it.get("object") != "page" or it.get("in_trash"):
                continue
            title = ""
            for v in it.get("properties", {}).values():
                if v.get("type") == "title":
                    title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                    break
            pages.append({
                "id": it["id"],
                "title": title or "(无标题)",
                "edited": it.get("last_edited_time", ""),
                "url": it.get("url", ""),
            })
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages

def fetch_markdown(page_id):
    """GET /v1/pages/{id}/markdown → (markdown_text, truncated)。"""
    resp = ntn_api(f"v1/pages/{page_id}/markdown")
    # 兼容两种返回形态：{"markdown": ...} 或 {"page_markdown": {"markdown": ...}}
    if "markdown" in resp:
        return resp["markdown"], resp.get("truncated", False)
    pm = resp.get("page_markdown", {})
    return pm.get("markdown", ""), pm.get("truncated", False)

def safe_name(title, page_id):
    s = re.sub(r'[\\/:*?"<>|]', "", title).strip()[:60] or "untitled"
    return f"{s}-{page_id.replace('-', '')[:8]}.md"

def main():
    full = "--full" in sys.argv
    state = {}
    if os.path.isfile(STATE_FILE) and not full:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

    pages = list_all_pages()
    print(f"search 到 {len(pages)} 个页面")

    changed = [p for p in pages if state.get(p["id"]) != p["edited"]]
    print(f"需同步 {len(changed)} 个（{'全量' if full else '增量'}）")

    # 清理已删除页面的旧文件
    live_ids = {p["id"] for p in pages}
    for pid, meta in list(state.items()):
        if pid not in live_ids and isinstance(meta, dict):
            old = os.path.join(OUT_DIR, meta.get("file", ""))
            if meta.get("file") and os.path.isfile(old):
                os.remove(old)
                print(f"删除已移除页面: {meta['file']}")

    ok, fail = 0, 0
    for p in changed:
        try:
            md, truncated = fetch_markdown(p["id"])
            header = (f"---\nnotion_id: {p['id']}\nurl: {p['url']}\n"
                      f"last_edited: {p['edited']}\n---\n\n# {p['title']}\n\n")
            fname = safe_name(p["title"], p["id"])
            with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
                f.write(header + md)
            state[p["id"]] = {"edited": p["edited"], "file": fname}
            ok += 1
            tag = " [截断]" if truncated else ""
            print(f"  [OK] {p['title'][:40]}{tag}")
            time.sleep(0.4)  # 限速 ~3 req/s
        except Exception as e:
            fail += 1
            print(f"  [FAIL] {p['title'][:40]}: {e}")

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print(f"完成: 成功 {ok}，失败 {fail}，状态文件已更新")

if __name__ == "__main__":
    main()
