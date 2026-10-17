"""记忆同步：经服务器 SSH 中转，在多台电脑间搬运 Alfred 的记忆数据。

架构：两台电脑各自本地跑 Alfred，服务器只做文件仓库（不跑任何服务），
且只碰 remote_dir 一个目录：

    remote_dir/
      current/            数据镜像（config.yaml / .env / rules / data / hist）
      previous/           上一次推送的快照（拉错可人工回滚）
      （last_sync.json 在 current/ 内：记录推送机器与时间）

传输：本地用 Python tarfile 打包 → ssh stdin；本地不依赖系统 tar/rsync，
Windows 10+ 与 macOS 自带 OpenSSH 客户端即可，零新 Python 依赖；远端需支持 tar。

纪律（顺序使用场景）：换机器前 push，到新机器先 pull 再聊；
同步时另一台的 Alfred 必须关闭（Qdrant/LanceDB 数据库文件不能两边同时写）。

v1 范围：只同步项目内条目，不含 config.paths.skills_dirs 中项目外的目录
（如 ~/.agents/skills）；skills 变动少，需要时可后续扩展。
"""

from __future__ import annotations

import io
import json
import platform
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .backup import create_backup
from .config import PROJECT_ROOT, Config

console = Console()

# 项目内参与同步的条目（不存在则跳过）；与 backup.PROJECT_ITEMS 保持一致
SYNC_ITEMS = ["config.yaml", ".env", "rules", "data", "hist"]

# tar 排除项：缓存目录与 Qdrant 残留锁（带过去会导致 mem0 初始化失败，
# 见 backup.py 与 AGENTS.md 坑点）
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {".lock"}

_MARKER = "last_sync.json"


class SyncError(RuntimeError):
    """同步失败（SSH / 打包 / 远端状态），message 面向用户。"""


def _resolve_host(config: Config) -> str:
    """服务器地址：config.yaml 的 sync.host 优先，回退到 .env 的 ALFRED_SYNC_HOST。

    推荐用 .env——config.yaml 会被提交进 git 仓库，服务器 IP 属于敏感信息
    （教训：曾因把填好的 host 留在 config.yaml 提交，IP 泄露进公开仓库历史）。
    """
    from os import environ
    return config.sync.host or environ.get("ALFRED_SYNC_HOST", "")


def _require_host(config: Config) -> str:
    host = _resolve_host(config)
    if not host:
        raise SyncError(
            "未配置服务器地址。在 .env 写入 ALFRED_SYNC_HOST=user@host（推荐，不进 git），"
            "或 config.yaml 的 sync.host。"
        )
    return host


def _ssh_base(config: Config) -> list[str]:
    # BatchMode：免密没配好时快速失败并给出可读错误，而不是卡在密码提示上
    args = ["ssh", "-p", str(config.sync.port),
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if config.sync.ssh_key:
        args += ["-i", str(Path(config.sync.ssh_key).expanduser())]
    args.append(_require_host(config))
    return args


def _remote_dir(config: Config) -> str:
    """远端目录的 shell 表达式：~ 前缀转成 $HOME 形式，避免引号抑制展开。"""
    d = config.sync.remote_dir.rstrip("/")
    if d.startswith("~/"):
        return '"$HOME"/' + shlex.quote(d[2:])
    if d == "~":
        return '"$HOME"'
    return shlex.quote(d)


def _tar_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = Path(ti.name).parts
    if any(p in EXCLUDE_DIRS for p in parts):
        return None
    if Path(ti.name).name in EXCLUDE_FILES:
        return None
    return ti


def _local_marker() -> dict:
    return {
        "host": platform.node() or "unknown",
        "time": datetime.now().isoformat(timespec="seconds"),
        "direction": "push",
    }


def _run_ssh_to_file(config: Config, remote_cmd: str, stdin_path: Path | None,
                     stdout_path: Path | None) -> None:
    """跑一次 ssh，stdin/stdout 走磁盘临时文件（避免管道生命周期坑）。

    出错时抛出带远端 stderr 的 SyncError。
    """
    cmd = _ssh_base(config) + [remote_cmd]
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    stdout = open(stdout_path, "wb") if stdout_path else subprocess.DEVNULL
    try:
        cp = subprocess.run(cmd, stdin=stdin, stdout=stdout,
                            stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise SyncError(
            f"找不到 ssh 命令：{exc}。Windows 请在「可选功能」中启用 OpenSSH 客户端。"
        )
    finally:
        for f in (stdin, stdout):
            if hasattr(f, "close"):
                f.close()
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace").strip()
        raise SyncError(
            f"SSH 失败（退出码 {cp.returncode}）：{err}\n"
            "排查：① 免密 key 登录是否配好（BatchMode 下不会提示输密码）；"
            "② sync.host / port / remote_dir 是否正确；③ 远端目录是否有写权限。"
        )


class PushBlocked(SyncError):
    """护栏拦截：服务器上是另一台机器的更新快照，本地未拉取。"""

    def __init__(self, remote: dict, local: dict | None):
        self.remote = remote
        self.local = local
        super().__init__(
            f"服务器快照来自 {remote.get('host', '?')}（{remote.get('time', '?')}），"
            "本地未同步过这份数据。"
        )


def _check_push_guard(config: Config, force: bool) -> None:
    """push 护栏：发现另一台机器的更新快照时拦截，交给用户决定。

    放行条件：远端标记来自本机（无分叉可能），或本地 marker 时间不早于
    远端（本地基于最新快照）；其余一律 PushBlocked，--force 可跳过。
    注意：护栏只拦截+提示，不做任何自动合并——先 pull / 强推 / 取消
    由用户当场选择。
    """
    remote = _fetch_marker(config)
    if remote is None:
        return  # 仅远端确认无标记时放行；SSH/标记异常由 _fetch_marker 直接报错
    if remote.get("host") == (platform.node() or "unknown"):
        return
    local = None
    local_file = PROJECT_ROOT / _MARKER
    if local_file.exists():
        try:
            local = json.loads(local_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            local = None
    # ISO 时间字符串同格式下字典序即时间序（两台机器各自本地时区）
    if local and local.get("time", "") >= remote.get("time", ""):
        return
    if force:
        console.print("[yellow]--force：跳过护栏，远端快照将退到 previous/。[/yellow]")
        return
    raise PushBlocked(remote, local)


def push(config: Config, *, force: bool = False) -> None:
    """本地 → 服务器：护栏检查 → 打包同步条目 → 远端原子轮换 current/previous。"""
    host = _require_host(config)
    _check_push_guard(config, force)
    items = [it for it in SYNC_ITEMS if (PROJECT_ROOT / it).exists()]
    if not items:
        raise SyncError(f"项目根 {PROJECT_ROOT} 下没有可同步的条目。")

    r = _remote_dir(config)
    console.print(f"[cyan]打包本地数据（{', '.join(items)}）…[/cyan]")

    with tempfile.TemporaryDirectory(prefix="alfred-sync-") as tmp:
        tarball = Path(tmp) / "payload.tar.gz"
        marker = _local_marker()
        with tarfile.open(tarball, "w:gz") as tar:
            for it in items:
                tar.add(PROJECT_ROOT / it, arcname=it, filter=_tar_filter)
            marker_bytes = json.dumps(marker, ensure_ascii=False).encode()
            ti = tarfile.TarInfo(_MARKER)
            ti.size = len(marker_bytes)
            ti.mtime = time.time()
            tar.addfile(ti, io.BytesIO(marker_bytes))

        size_mb = tarball.stat().st_size / 1024 / 1024
        console.print(
            f"[cyan]推送 {size_mb:.1f}MB → {host}:{config.sync.remote_dir} …[/cyan]"
        )
        remote_cmd = (
            f"set -e; "
            f"rm -rf {r}/tmp; mkdir -p {r}/tmp; "
            f"tar xzf - -C {r}/tmp; "
            f"rm -rf {r}/previous; "
            f"if [ -d {r}/current ]; then mv {r}/current {r}/previous; fi; "
            f"mv {r}/tmp {r}/current"
        )
        _run_ssh_to_file(config, remote_cmd, stdin_path=tarball, stdout_path=None)

    # 本地也留一份标记，作为下次护栏判定的快照基线
    (PROJECT_ROOT / _MARKER).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print("[green]推送完成。[/green] 服务器已轮换 current/previous。")


def _fetch_marker(config: Config) -> dict | None:
    r = _remote_dir(config)
    marker_path = f"{r}/current/{_MARKER}"
    try:
        cp = subprocess.run(
            _ssh_base(config) + [
                f"if [ -f {marker_path} ]; then cat {marker_path}; fi"
            ],
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise SyncError(
            f"找不到 ssh 命令：{exc}。Windows 请在「可选功能」中启用 OpenSSH 客户端。"
        ) from exc
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace").strip()
        raise SyncError(f"读取远端同步标记失败（SSH 退出码 {cp.returncode}）：{err}")
    raw = cp.stdout.strip()
    if not raw:
        return None
    try:
        marker = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError(
            "远端同步标记 last_sync.json 已损坏，无法安全判断快照状态。"
        ) from exc
    if (
        not isinstance(marker, dict)
        or not isinstance(marker.get("host"), str)
        or not marker["host"]
        or not isinstance(marker.get("time"), str)
        or not marker["time"]
    ):
        raise SyncError(
            "远端同步标记 last_sync.json 格式无效，无法安全判断快照状态。"
        )
    return marker


def pull(config: Config) -> None:
    """服务器 → 本地：先自动备份本地数据，再拉取解压逐项替换。"""
    host = _require_host(config)

    marker = _fetch_marker(config)
    if marker is None:
        raise SyncError(
            "服务器上还没有同步数据。先在有数据的机器上执行 alfred sync push。"
        )
    console.print(
        f"[cyan]远端快照：{marker.get('host', '?')} 推送于 {marker.get('time', '?')}[/cyan]"
    )

    # 保险：pull 前备份本地，防止未 push 的记忆被覆盖后无法找回
    console.print("[cyan]pull 前自动备份本地数据…[/cyan]")
    backup_path, count = create_backup(config)
    console.print(f"[dim]本地已备份（{count} 个文件）：{backup_path}[/dim]")

    r = _remote_dir(config)
    with tempfile.TemporaryDirectory(prefix="alfred-sync-") as tmp:
        tarball = Path(tmp) / "payload.tar.gz"
        staging = Path(tmp) / "staging"
        staging.mkdir()

        console.print(f"[cyan]从 {host} 拉取…[/cyan]")
        _run_ssh_to_file(config, f"tar czf - -C {r}/current .",
                         stdin_path=None, stdout_path=tarball)

        # 远端是自己的服务器，来源可信；3.12+ 仍启用 data 过滤兜底
        extract_kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(staging, **extract_kwargs)

        replaced = []
        for it in SYNC_ITEMS + [_MARKER]:
            src = staging / it
            if not src.exists():
                continue
            dst = PROJECT_ROOT / it
            if dst.is_dir():
                shutil.rmtree(dst)
            elif dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
            replaced.append(it)

    console.print(f"[green]拉取完成。[/green] 已替换：{', '.join(replaced)}")
    console.print("[dim]如拉错版本，可用备份 zip 或服务器 previous/ 目录回滚。[/dim]")


def status(config: Config) -> None:
    """显示服务器上最近一次推送的信息，帮助判断该 push 还是该 pull。"""
    _require_host(config)
    marker = _fetch_marker(config)
    if marker is None:
        console.print("[yellow]服务器上还没有同步数据。[/yellow]")
        return
    console.print(
        f"上次推送：[bold]{marker.get('host', '?')}[/bold] "
        f"于 {marker.get('time', '?')}"
    )
    local = PROJECT_ROOT / _MARKER
    if local.exists():
        try:
            lm = json.loads(local.read_text(encoding="utf-8"))
            console.print(
                f"本机当前快照来源：{lm.get('host', '?')} 推送于 {lm.get('time', '?')}"
            )
        except json.JSONDecodeError:
            pass
