"""Retained streaming markdown renderer for a scrolling terminal.

Why this exists (and why not Rich ``Live``)
-------------------------------------------
Rich ``Live`` redraws each frame by erasing the *previous* frame with a
multi-line cursor-up (``\\x1b[<N>A``) whose ``N`` is tracked by Live's own
internal accounting. In some terminals — especially with CJK text and tables —
that accounting lands on ~0, so nothing is erased and every refresh *appends*
a full copy of the accumulated markdown (the "blowup" / snowball). A
single-line spinner never hit this because it only cursor-ups 1.

This module reuses Rich *only to render* (``Console.capture()`` of
``console.print(Markdown(text))`` returns the exact styled, wrapped lines —
CJK wraps and table boxes included), but drives the erase ourselves: we
always erase exactly the number of lines we last wrote, so the count can
never be wrong. No snowball is possible in any branch.

The one case where erase-above-the-viewport is impossible — a frame taller
than the terminal — is handled by *locking*: we stop live re-rendering and
dump the final markdown as a plain append on ``close()``. Pathological inputs
degrade gracefully instead of stacking.

Live frames and the final frame both go through the same render console and
the same ``emit`` path, so the segment never "snaps" between two styles — it
is styled markdown throughout, growing token by token.
"""

from __future__ import annotations

import time
from typing import Callable, List

from rich.console import Console
from rich.markdown import Markdown

CUR_UP = "\x1b[{n}A"      # cursor up n lines
CR = "\r"
CLEAR_TO_END = "\x1b[J"   # erase from cursor to end of screen (CSI 0J)


class StreamMarkdown:
    """In-place streaming markdown for one assistant segment.

    The caller feeds text deltas via :meth:`update` as they arrive, then
    finalizes with :meth:`close` (which erases the live partial and appends the
    final, fully-rendered markdown). Between calls the rendered frame stays on
    screen; each ``update`` erases exactly the previous frame and redraws, so
    the user sees styled markdown grow token by token.

    All cursor math is driven by line counts obtained from Rich's own renderer
    (``Console.capture``), never by Rich ``Live``'s internal accounting. Erase
    count == last drawn line count, always.
    """

    def __init__(
        self,
        console: Console,
        emit: Callable[[str], None],
        *,
        term_width: int,
        term_height: int,
        throttle_s: float = 0.03,
    ) -> None:
        self._emit = emit
        self._term_height = max(3, term_height)
        # Render one column narrower than the terminal so full-width table
        # boxes (which span the render width exactly) never trigger the
        # terminal's auto-wrap. Auto-wrap drops the cursor onto a line we did
        # not account for, which is the root of cursor-tracking drift. The
        # 1-col margin removes that ambiguity; every line we emit ends at a
        # deterministic column. A dedicated render console carries the real
        # console's color system, so live frames are styled just like a normal
        # console.print(Markdown) would be.
        self._render_width = max(20, term_width - 1)
        self._rcon = Console(
            width=self._render_width,
            color_system=console.color_system,
            force_terminal=True,
            file=__import__("io").StringIO(),
        )
        self._throttle = throttle_s
        self._text: str = ""
        self._shown: int = 0           # screen lines currently on display
        self._locked: bool = False    # True once a frame would exceed the viewport
        self._last_render: float = 0.0
        self._dirty: bool = False

    # -- rendering ---------------------------------------------------------
    def _frame_lines(self, text: str) -> List[str]:
        """Exact styled screen lines for the current segment text."""
        with self._rcon.capture() as cap:
            self._rcon.print(Markdown(text))
        out = cap.get()
        if out.endswith("\n"):
            out = out[:-1]
        return out.split("\n")

    # -- low-level terminal ops -------------------------------------------
    def _erase(self, n: int) -> None:
        # Cursor is at col 0 of the line *below* our frame (we always emit a
        # trailing newline after a draw). Up n -> top of our frame; clear-to-end
        # wipes the frame plus the blank line below. Exact for any n < height.
        self._emit(CR)
        if n > 0:
            self._emit(CUR_UP.format(n=n))
        self._emit(CLEAR_TO_END)

    def _draw(self, lines: List[str]) -> None:
        # trailing newline -> cursor lands at col 0 of the line below the frame,
        # which is the invariant _erase relies on.
        self._emit("\n".join(lines) + "\n")

    # -- public API --------------------------------------------------------
    def update(self, delta: str) -> None:
        if not delta:
            return
        self._text += delta
        self._dirty = True
        # Render the first frame immediately so the user sees something fast,
        # then throttle subsequent frames to avoid per-token full redraws.
        now = time.monotonic()
        if self._shown == 0 or now - self._last_render >= self._throttle:
            self._last_render = now
            self._flush()

    def _flush(self) -> None:
        if not self._dirty or self._locked:
            return
        self._dirty = False
        lines = self._frame_lines(self._text)
        n = len(lines)
        if n >= self._term_height:
            # Would overflow the viewport: erase the partial we have (safe,
            # it's < height) and stop live re-rendering. The final markdown is
            # appended on close(), so nothing doubles up.
            self._erase(self._shown)
            self._shown = 0
            self._locked = True
            return
        self._erase(self._shown)
        self._draw(lines)
        self._shown = n

    def close(self) -> None:
        """Finalize: erase the live partial and append the final markdown.

        Idempotent. The final frame uses the same render console and emit path
        as the live frames, so there is no style/width snap at the end.
        """
        if self._shown or self._locked:
            self._erase(self._shown)
            self._shown = 0
            self._locked = False
        self._dirty = False
        text = self._text
        self._text = ""
        if text.strip():
            self._emit("\n".join(self._frame_lines(text)) + "\n")
