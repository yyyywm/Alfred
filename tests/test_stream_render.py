"""No-snowball regression test for StreamMarkdown.

We model a tiny VT100-ish terminal (virtual screen), feed it the raw bytes the
renderer emits while streaming a markdown reply in many small chunks, and
assert invariants the old Rich ``Live`` approach violated:

1. The chunked-streaming final screen equals the screen from rendering the
   whole reply in one shot — i.e. streaming converges to a single clean copy,
   with no stacked duplicates of the intro line / table rows.
2. Screen height never grows unbounded during streaming — it stays within the
   viewport (locking when a frame would overflow) instead of climbing by a full
   copy per refresh.
3. A reply taller than the terminal locks (no live erase above the viewport)
   yet still ends as the markdown rendered once.
"""

from __future__ import annotations

from rich.console import Console

from alfred._stream_render import StreamMarkdown


class _ScreenFile:
    """File-like Rich's Console can print to, routing bytes to the screen."""

    def __init__(self, screen: "VirtualScreen") -> None:
        self._screen = screen

    def write(self, data: str) -> int:
        self._screen.apply(data)
        return len(data)

    def flush(self) -> None:  # noqa: D401
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
    """Minimal VT100 screen: \\r, \\n, CSI cursor-up (A), CSI 0J / 2K.

    SGR (``\\x1b[...m``) sequences are consumed but ignored — we compare the
    visible text, and ignoring styling does not affect line counts.
    """

    def __init__(self) -> None:
        self.lines: list[list[str]] = [[]]
        self.row = 0
        self.col = 0

    def _ensure(self, r: int) -> None:
        while len(self.lines) <= r:
            self.lines.append([])

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
                self.row += 1
                self.col = 0
                self._ensure(self.row)
                i += 1
                continue
            self._ensure(self.row)
            line = self.lines[self.row]
            if self.col < len(line):
                line[self.col] = ch
            else:
                line.append(ch)
            self.col += 1
            i += 1

    def _csi(self, params: str, final: str) -> None:
        n = int(params) if params.isdigit() else 1
        if final == "A":  # cursor up
            self.row = max(0, self.row - n)
        elif final == "J":  # 0J = erase cursor to end of screen
            self._ensure(self.row)
            del self.lines[self.row][self.col:]
            del self.lines[self.row + 1:]
        elif final == "K":  # erase in line (2K = whole line)
            self._ensure(self.row)
            if n == 2:
                self.lines[self.row].clear()
                self.col = 0
            else:
                del self.lines[self.row][self.col:]
        # SGR (m) and anything else: ignored, but the sequence is consumed above.

    def snapshot(self) -> list[str]:
        return ["".join(line) for line in self.lines]

    def height(self) -> int:
        return len(self.lines)


def _feed(
    text: str,
    *,
    term_width: int,
    term_height: int,
    chunk: int = 5,
) -> tuple[VirtualScreen, list[int]]:
    screen = VirtualScreen()

    def emit(s: str) -> None:
        screen.apply(s)

    con = Console(width=term_width, force_terminal=False, file=_ScreenFile(screen))
    sm = StreamMarkdown(
        con, emit,
        term_width=term_width, term_height=term_height, throttle_s=0.0,
    )

    heights: list[int] = []
    for i in range(0, len(text), chunk):
        sm.update(text[i:i + chunk])
        heights.append(screen.height())
    sm.close()
    return screen, heights


def _rstrip(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out


def _final(text: str, *, term_width: int, term_height: int, chunk: int = 5) -> list[str]:
    screen, _ = _feed(
        text, term_width=term_width, term_height=term_height, chunk=chunk
    )
    return _rstrip(screen.snapshot())


# A representative reply: heading, CJK paragraph with bold, and a table — the
# exact shape that triggered the Rich Live blowup (stacked intro + table rows).
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
        f"chunked ({len(chunked)} lines):\n{chr(10).join(chunked)}"
    )


def test_no_snowball_height_bounded_during_stream():
    # The old Rich Live blowup stacked a full copy per refresh, so screen height
    # grew ~ linearly with chunk count. Here it must stay within the viewport.
    _screen, heights = _feed(REPLY, term_width=44, term_height=20)
    assert max(heights) <= 20, f"screen grew unbounded: max height {max(heights)}"
    # And it must not climb monotonically with more chunks (that'd be stacking).
    assert heights[-1] <= max(heights) + 1


def test_tall_reply_locks_and_still_renders_once():
    # Reply taller than the terminal: must lock (no live erase above viewport)
    # and still end as the markdown rendered once.
    _screen, heights = _feed(REPLY, term_width=44, term_height=4)
    assert max(heights) <= 4, f"lock failed, screen grew past viewport: {max(heights)}"
    chunked = _final(REPLY, term_width=44, term_height=4, chunk=5)
    single = _final(REPLY, term_width=44, term_height=100, chunk=len(REPLY))
    assert chunked == single, "tall reply must still render the markdown once"
