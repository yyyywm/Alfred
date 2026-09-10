"""回归：chat 的 Rich Live spinner 与 longterm.add_async 的 stdout 重定向交错。

真实事故（2026-09-10）：mem0 写入的 devnull redirect 窗口横跨数十秒，下一轮
`console.status(...)` 在窗口内 start()，Rich Live 把 devnull 保存为"原流"；窗口
退出后 devnull 关闭，status.stop() 无条件把"原流"装回 sys.stdout → 主线程
console.print 报 ValueError: I/O operation on closed file（进程崩溃/静默退出）。
"""

import io
import os
import sys

from rich.console import Console

from alfred.cli import _make_status


def test_status_stop_does_not_restore_closed_devnull():
    console = Console(file=io.StringIO(), force_terminal=True)
    real_stdout = sys.stdout
    real_stderr = sys.stderr
    # add_async 的 devnull redirect 窗口先开（mem0 抽取耗时数十秒）
    devnull = open(os.devnull, "w", encoding="utf-8")
    saved_by_window_out, saved_by_window_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = devnull, devnull
    try:
        status = _make_status("思考中")
        status.start()  # 修复前：Live 把 devnull 存为"原流"
        # 窗口退出：恢复真实流并关闭 devnull
        sys.stdout, sys.stderr = saved_by_window_out, saved_by_window_err
        devnull.close()
        status.stop()  # 修复前：把已关闭的 devnull 装回 sys.stdout 并写它 → ValueError
        assert sys.stdout is real_stdout
        assert sys.stderr is real_stderr
        assert not sys.stdout.closed
    finally:
        # 无论成败都恢复真实流，避免污染 pytest 捕获
        if sys.stdout is not real_stdout:
            sys.stdout = real_stdout
        if sys.stderr is not real_stderr:
            sys.stderr = real_stderr
