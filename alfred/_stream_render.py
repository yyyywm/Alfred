"""Retained streaming markdown renderer for a scrolling terminal.

Why this exists (and why not Rich ``Live``)
-------------------------------------------
Rich ``Live`` redraws each frame by erasing the *previous* frame with a
multi-line cursor-up whose ``N`` is its own internal accounting. In some
terminals — CJK + tables — that accounting lands on ~0, so nothing is erased
and every refresh *appends* a full copy (the "blowup" / snowball).

A naive "erase the whole frame, redraw" fix (this module's first version) is
also broken, for a subtler reason: in a *scrolling* terminal the cursor sits
near the bottom of the viewport once there is any chat history above. As soon
as a frame is taller than the room below the cursor, drawing it *scrolls*,
and ``cursor-up N`` can no longer reach the frame's top (it has scrolled into
scrollback). The next erase then ``clear-to-end``s the wrong region — the
visible screen goes blank, and the final markdown is dumped once at ``close``.
Exactly the "blank then dump" failure.

The design here
---------------
Two observations make streaming markdown work in a scrolling terminal:

1. **Commit completed blocks; re-render only the in-progress block.**
   Markdown is block-structured (headings, paragraphs, tables, …). A block is
   "complete" once a blank line (``\\n\\n``) follows it; its rendering is then
   final and will not reflow as more text arrives. We commit completed blocks
   to scrollback once (plain append, never touched again) and keep only the
   *last, in-progress* block live, re-rendering it in place each flush. This is
   what makes tables correct: a table is one block, re-rendered whole as rows
   arrive (column widths settle), and committed only once it ends.

2. **A live block no taller than the screen is always erasable.**
   When a block of ``L`` lines (``L < terminal height``) is drawn, it lands at
   the bottom of the viewport — either in place (room below the cursor) or by
   scrolling earlier content into scrollback. In both cases, after the draw
   the block occupies the bottom ``L`` rows and the cursor rests one line
   below it, so ``cursor-up L`` reliably reaches the block's top. Erase =
   ``cursor-up L`` + clear-to-end is therefore exact for any ``L < height``.

For a pathological single block taller than the terminal, erase-above-viewport
is impossible, so we *lock*: stop live re-rendering and append the final
markdown on ``close()``. Nothing ever doubles up.

Live frames and the final frame both go through the same render console and
emit path, so a segment never "snaps" between two styles — styled markdown
throughout, growing block by block.
"""

from __future__ import annotations

import time
from typing import Callable, List

from rich.console import Console
from rich.markdown import Markdown

CUR_UP = "\x1b[{n}A"       # cursor up n lines
CR = "\r"
CLEAR_BELOW = "\x1b[J"     # erase cursor -> end of screen (CSI 0J)


class StreamMarkdown:
    """In-place streaming markdown for one assistant segment.

    Feed text deltas via :meth:`update` as they arrive, finalize with
    :meth:`close` (which commits any still-live block). Completed blocks are
    appended to scrollback; the in-progress block is re-rendered in place, so
    the user sees styled markdown grow block by block.
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
        self._height = max(3, term_height)
        # Render one column narrower than the terminal so full-width table
        # boxes (which span the render width exactly) never trigger the
        # terminal's auto-wrap. Auto-wrap drops the cursor onto an unaccounted
        # line, which is the root of cursor-tracking drift. The 1-col margin
        # removes that ambiguity; every line we emit ends at a deterministic
        # column. The render console carries the real console's color system so
        # live frames are styled exactly like a normal console.print(Markdown).
        self._render_width = max(20, term_width - 1)
        self._rcon = Console(
            width=self._render_width,
            color_system=console.color_system,
            force_terminal=True,
            file=__import__("io").StringIO(),
        )
        self._throttle = throttle_s
        self._text: str = ""
        self._committed: int = 0      # rendered lines already in scrollback
        self._live_h: int = 0        # lines of the in-place live block on screen
        self._locked: bool = False   # True once the live block overflows the viewport
        self._last_render: float = 0.0
        self._dirty: bool = False

    # -- rendering ---------------------------------------------------------
    def _frame_lines(self, text: str) -> List[str]:
        """Exact styled screen lines for ``text``."""
        with self._rcon.capture() as cap:
            self._rcon.print(Markdown(text))
        out = cap.get()
        if out.endswith("\n"):
            out = out[:-1]
        return out.split("\n")

    def _stable_prefix_len(self, full: List[str]) -> int:
        """How many leading lines of ``full`` are finalized blocks.

        A block finalizes at a blank line (``\\n\\n``). Everything up to and
        including the last such break renders to stable lines that will not
        reflow as more text arrives. We render that prefix separately and
        confirm it matches the head of ``full`` (it must, for independent
        markdown blocks); if it ever does not, we keep everything live rather
        than risk committing a divergent prefix.
        """
        idx = self._text.rfind("\n\n")
        if idx < 0:
            return 0
        stable = self._frame_lines(self._text[: idx + 2])
        # Trailing blank lines are ambiguous (Rich may trim them differently
        # when more content follows), so commit only the non-blank head.
        k = len(stable)
        while k > 0 and stable[k - 1] == "":
            k -= 1
        if k > len(full):
            return 0
        for i in range(k):
            if full[i] != stable[i]:
                return 0
        return k

    # -- low-level terminal ops -------------------------------------------
    def _erase_live(self) -> None:
        """Erase the in-place live block (``self._live_h`` lines)."""
        if self._live_h > 0:
            self._emit(CR + CUR_UP.format(n=self._live_h) + CLEAR_BELOW)
            self._live_h = 0

    def _commit(self, lines: List[str]) -> None:
        """Append lines to scrollback permanently (each followed by a newline)."""
        if lines:
            self._emit("\n".join(lines) + "\n")

    # -- public API --------------------------------------------------------
    def update(self, delta: str) -> None:
        if not delta:
            return
        self._text += delta
        self._dirty = True
        # First frame renders immediately for fast feedback; later frames are
        # throttled to avoid per-token full redraws.
        now = time.monotonic()
        if self._live_h == 0 and not self._locked or now - self._last_render >= self._throttle:
            self._last_render = now
            self._flush()

    def _flush(self) -> None:
        if not self._dirty or self._locked:
            return
        self._dirty = False
        full = self._frame_lines(self._text)

        # Newly-finalized blocks: commit their lines (never de-commit).
        commit_to = max(self._stable_prefix_len(full), self._committed)
        # The in-progress block, i.e. everything after what is finalized.
        live = full[commit_to:]
        L = len(live)

        will_commit = commit_to > self._committed
        will_draw = L > 0 and L < self._height  # fits the viewport → in place

        # Erasing makes room: newly-committed lines and the new in-place block
        # both start where the old live block was. Crucially, we do NOT erase
        # when we're about to lock (L >= height): erase-above-viewport is
        # impossible there, so erasing would only blank the last good render.
        # Keeping it (stale, but visible) is far better than a blank viewport.
        if will_commit or will_draw:
            self._erase_live()

        if will_commit:
            self._commit(full[self._committed : commit_to])
            self._committed = commit_to

        if L == 0:
            return

        if will_draw:
            # Fits the viewport: re-render the in-progress block in place.
            self._emit("\n".join(live) + "\n")
            self._live_h = L
        else:
            # Block reaches/exceeds the viewport: cursor-up can no longer
            # reach a top that has scrolled off, so in-place erase would
            # snowball. Lock — the last in-place render stays visible (we did
            # not erase) and close() emits the final markdown.
            self._locked = True

    def close(self) -> None:
        """Finalize: erase any live block and commit whatever is left.

        Emits only the *uncommitted* remainder (``full[self._committed:]``),
        never the full text — completed blocks were already appended to
        scrollback as they finalized, so re-emitting the whole document would
        double them (most visibly in the lock path, where much was committed
        before the live block overflowed). Idempotent.
        """
        self._erase_live()
        self._locked = False
        self._dirty = False
        text = self._text
        committed = self._committed
        self._text = ""
        self._committed = 0
        if text.strip():
            remaining = self._frame_lines(text)[committed:]
            if remaining:
                self._emit("\n".join(remaining) + "\n")
