"""Scrolling-terminal regression tests for StreamMarkdown.

The previous version of this file modeled an *infinite-height* screen that
started with the cursor at row 0. That never reproduced the real failure — a
scrolling terminal with chat history above — so the old "erase the whole
frame, redraw" code passed here while blanking the user's actual terminal.

``VirtualScreen`` now has a fixed height and *scrolls*: once the cursor reaches
the bottom row, writing more lines shifts the top line into ``scrollback`` and
keeps the cursor pinned to the bottom — exactly what a real terminal does.
``CSI A`` (cursor-up) clamps at row 0, so it cannot reach a frame that has
scrolled off-screen; ``CSI 0J`` only clears the visible viewport. These two
facts are what broke the whole-frame erase, and the tests below exercise them.

We assert:

1. Chunked streaming converges to the single-shot render — no stacked copies.
2. With chat history prefilled (cursor near the bottom) and a reply tall
   enough to scroll, streaming still converges AND the visible viewport is
   never left entirely blank mid-stream (the "blank then dump" bug).
3. A reply taller than the terminal locks (no erase above the viewport) and
   still ends as the markdown rendered exactly once.
"""

from __future__ import annotations

from rich.console import Console

from alfred._stream_render import StreamMarkdown


class _ScreenFile:
    """File-like Rich can print to, routing bytes to the screen."""

    def __init__(self, screen: "VirtualScreen") -> None:
        self._screen = screen

    def write(self, data: str) -> int:
        self._screen.apply(data)
        return len(data)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


class VirtualScreen:
    """A finite-height VT100 screen that scrolls, like a real terminal.

    Models exactly the behaviors that matter for streaming renderers:

    * Fixed ``height`` rows; once the cursor is on the bottom row, a newline
      (or an auto-wrap) *scrolls* — the top row moves to ``scrollback`` and a
      fresh blank row appears at the bottom. The cursor stays on the bottom.
    * ``CSI A`` (cursor up) clamps at row 0 — it can never reach content that
      has scrolled into scrollback. This is the crux of the blank bug.
    * ``CSI 0J`` erases from the cursor to the end of the *visible viewport*
      only (never scrollback); ``CSI 0K``/``2K`` erase within the current row.
    * SGR (``CSI …m``) is consumed and ignored — we compare visible text.

    ``snapshot()`` returns ``scrollback + visible rows`` (the whole document
    as a terminal's scrollback buffer would show it).
    """

    def __init__(self, *, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.rows: list[list[str]] = [[] for _ in range(height)]
        self.scrollback: list[str] = []
        self.row = 0
        self.col = 0

    # -- internals ---------------------------------------------------------
    def _newline(self) -> None:
        if self.row >= self.height - 1:
            self.scrollback.append("".join(self.rows[0]))
            self.rows.pop(0)
            self.rows.append([])
            self.row = self.height - 1
        else:
            self.row += 1
        self.col = 0

    def _put(self, ch: str) -> None:
        if self.col >= self.width:
            self._newline()
        line = self.rows[self.row]
        if self.col < len(line):
            line[self.col] = ch
        else:
            line.append(ch)
        self.col += 1

    def _csi(self, params: str, final: str) -> None:
        n = int(params) if params.isdigit() else 1
        if final == "A":  # cursor up — clamps at row 0 (can't reach scrollback)
            self.row = max(0, self.row - n)
        elif final == "J":  # 0J: erase cursor -> end of visible viewport
            self.rows[self.row][self.col:] = []
            del self.rows[self.row + 1:]
            while len(self.rows) < self.height:
                self.rows.append([])
        elif final == "K":  # 0K/2K: erase within the current row
            if n == 2:
                self.rows[self.row] = []
                self.col = 0
            else:
                self.rows[self.row][self.col:] = []
        # SGR (m) and anything else: ignored (sequence already consumed).

    # -- public ------------------------------------------------------------
    def apply(self, data: str) -> None:
        i, n = 0, len(data)
        while i < n:
            ch = data[i]
            if ch == "\x1b":
                j = i + 1
                if j < n and data[j] == "[":
                    j += 1
                    params = ""
                    while j < n and (data[j].isdigit() or data[j] == ";"):
                        params += data[j]
                        j += 1
                    if j < n:
                        self._csi(params, data[j])
                        i = j + 1
                        continue
                i = j
                continue
            if ch == "\r":
                self.col = 0
                i += 1
                continue
            if ch == "\n":
                self._newline()
                i += 1
                continue
            self._put(ch)
            i += 1

    def visible(self) -> list[str]:
        return ["".join(r) for r in self.rows]

    def snapshot(self) -> list[str]:
        return self.scrollback + self.visible()

    def height(self) -> int:
        return len(self.rows)


def _feed(
    text: str,
    *,
    term_width: int,
    term_height: int,
    chunk: int = 5,
    prefill: int = 0,
) -> tuple[VirtualScreen, list[int], list[list[str]]]:
    """Stream ``text`` in chunks; return (screen, visible-heights, visibles).

    ``prefill`` writes that many plain history lines first so the cursor
    starts near the bottom of the viewport — modeling a chat with history
    above, which is the condition that triggered the blank bug.
    """
    screen = VirtualScreen(width=term_width, height=term_height)

    def emit(s: str) -> None:
        screen.apply(s)

    con = Console(width=term_width, force_terminal=False, file=_ScreenFile(screen))
    sm = StreamMarkdown(
        con, emit,
        term_width=term_width, term_height=term_height, throttle_s=0.0,
    )

    for i in range(prefill):
        screen.apply(f"历史第 {i + 1} 行\n")

    heights: list[int] = []
    visibles: list[list[str]] = []
    for i in range(0, len(text), chunk):
        sm.update(text[i:i + chunk])
        visibles.append(screen.visible())
        heights.append(sum(1 for r in screen.visible() if r.strip()))
    sm.close()
    return screen, heights, visibles


def _rstrip(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out


def _final(
    text: str,
    *,
    term_width: int,
    term_height: int,
    chunk: int = 5,
    prefill: int = 0,
) -> list[str]:
    screen, _h, _v = _feed(
        text, term_width=term_width, term_height=term_height, chunk=chunk,
        prefill=prefill,
    )
    return _rstrip(screen.snapshot())


# A representative reply: heading, CJK paragraph with bold, and a table — the
# exact shape that triggered the original Rich Live blowup, and whose table
# column widths *reflow* as wide rows ("7165空心杯") arrive mid-stream.
REPLY = (
    "# 反应轮摆硬件清单\n\n"
    "这版只讲**硬件和结构**。空心杯电机选 7165，推力足够。\n\n"
    "| 部件 | 型号 | 重量 |\n"
    "|---|---|---|\n"
    "| 电机 | 7165空心杯 | 12g |\n"
    "| 桨 | 5寸 | 4g |\n"
    "| 驱动 | ESC 6A | 6g |\n"
    "| 杆 | 碳纤管 | 20g |\n"
    "\n总重约 42g。"
)


def test_chunked_stream_converges_to_single_render():
    # Streaming the reply in small chunks must end at the exact same screen as
    # rendering the whole reply in one shot. A snowball would leave stacked
    # copies and fail this.
    chunked = _final(REPLY, term_width=44, term_height=20, chunk=5)
    single = _final(REPLY, term_width=44, term_height=100, chunk=len(REPLY))
    assert chunked == single, (
        "chunked stream did not converge to a single render\n"
        f"chunked ({len(chunked)} lines):\n{chr(10).join(chunked)}\n"
        f"single ({len(single)} lines):\n{chr(10).join(single)}"
    )


# Substrings that only appear in the *reply* (not in the prefilled history
# lines), used to prove the reply itself is streaming to the viewport.
_REPLY_MARKERS = ["反应轮", "7165", "空心杯", "碳纤管", "42g", "硬件和结构", "ESC"]


def _reply_on_screen(visible: list[str]) -> bool:
    return any(m in r for r in visible for m in _REPLY_MARKERS)


def test_streams_reply_visible_with_history_and_scroll():
    # The real failure: cursor near the bottom (chat history above) and a reply
    # tall enough that the OLD whole-frame code locked (frame >= height) and
    # erased-without-redrawing — so the reply vanished mid-stream ("blank then
    # dump"). Here height=10: the full reply (~15 lines) overflows the viewport
    # (so the old code locks), yet *each block* is < 10 lines (so the new code
    # streams every block in place and never locks). The reply must therefore
    # stay visible on screen at every step after it begins, and the final
    # document (minus prefilled history) must equal the single-shot render.
    prefill = 4
    screen, _h, visibles = _feed(
        REPLY, term_width=44, term_height=10, chunk=5, prefill=prefill,
    )

    seen_reply = False
    for i, vis in enumerate(visibles):
        if _reply_on_screen(vis):
            seen_reply = True
        assert not seen_reply or _reply_on_screen(vis), (
            f"reply vanished from the viewport at step {i} — the blank-then-dump bug"
        )

    assert seen_reply, "reply never appeared on screen at all"
    final = _rstrip(screen.snapshot())[prefill:]
    single = _final(REPLY, term_width=44, term_height=100, chunk=len(REPLY))
    assert final == single, (
        "scrolling stream did not converge to a single render\n"
        f"final ({len(final)} lines):\n{chr(10).join(final)}\n"
        f"single ({len(single)} lines):\n{chr(10).join(single)}"
    )


def test_tall_reply_locks_and_still_renders_once():
    # Reply taller than the terminal: must lock (no live erase above viewport)
    # and still end as the markdown rendered exactly once.
    _screen, heights, _visibles = _feed(REPLY, term_width=44, term_height=4)
    assert max(heights) <= 4, f"lock failed, visible grew past viewport: {max(heights)}"
    chunked = _final(REPLY, term_width=44, term_height=4, chunk=5)
    single = _final(REPLY, term_width=44, term_height=100, chunk=len(REPLY))
    assert chunked == single, "tall reply must still render the markdown once"
