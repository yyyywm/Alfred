"""SSH 记忆同步测试（mock 子进程，不访问真实服务器）。"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from alfred import sync
from alfred.config import Config, SyncConfig


def _cfg(host: str = "user@server") -> Config:
    return Config(sync=SyncConfig(host=host))


def _cp(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess(
        args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_fetch_marker_returns_none_for_confirmed_absence(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _cp()

    monkeypatch.setattr(sync.subprocess, "run", fake_run)

    assert sync._fetch_marker(_cfg()) is None
    assert calls[0][-1].startswith("if [ -f ")


def test_fetch_marker_raises_on_ssh_failure(monkeypatch):
    monkeypatch.setattr(
        sync.subprocess,
        "run",
        lambda *args, **kwargs: _cp(255, stderr=b"connection refused"),
    )

    with pytest.raises(sync.SyncError) as exc_info:
        sync._fetch_marker(_cfg())

    assert "255" in str(exc_info.value)
    assert "connection refused" in str(exc_info.value)


def test_fetch_marker_reports_missing_ssh(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)

    with pytest.raises(sync.SyncError, match="找不到 ssh"):
        sync._fetch_marker(_cfg())


@pytest.mark.parametrize(
    "payload",
    [b"{bad", b"\xff", b"[]", b'{"host": "", "time": ""}'],
)
def test_fetch_marker_rejects_invalid_marker(monkeypatch, payload):
    monkeypatch.setattr(
        sync.subprocess, "run", lambda *args, **kwargs: _cp(stdout=payload)
    )

    with pytest.raises(sync.SyncError, match="同步标记"):
        sync._fetch_marker(_cfg())


def test_push_guard_blocks_unpulled_remote(monkeypatch, tmp_path):
    remote = {"host": "other-machine", "time": "2026-10-17T22:00:00"}
    monkeypatch.setattr(sync, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sync.platform, "node", lambda: "local-machine")
    monkeypatch.setattr(sync, "_fetch_marker", lambda config: remote)

    with pytest.raises(sync.PushBlocked):
        sync._check_push_guard(_cfg(), force=False)


def test_push_guard_allows_pulled_remote(monkeypatch, tmp_path):
    remote = {"host": "other-machine", "time": "2026-10-17T22:00:00"}
    (tmp_path / sync._MARKER).write_text(json.dumps(remote), encoding="utf-8")
    monkeypatch.setattr(sync, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sync.platform, "node", lambda: "local-machine")
    monkeypatch.setattr(sync, "_fetch_marker", lambda config: remote)

    sync._check_push_guard(_cfg(), force=False)


def test_push_guard_blocks_different_snapshot_even_with_later_time(monkeypatch, tmp_path):
    remote = {"host": "other-machine", "time": "2026-10-17T22:00:00"}
    local = {"host": "local-machine", "time": "2099-01-01T00:00:00"}
    (tmp_path / sync._MARKER).write_text(json.dumps(local), encoding="utf-8")
    monkeypatch.setattr(sync, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sync.platform, "node", lambda: "local-machine")
    monkeypatch.setattr(sync, "_fetch_marker", lambda config: remote)

    with pytest.raises(sync.PushBlocked):
        sync._check_push_guard(_cfg(), force=False)


def test_push_guard_does_not_swallow_status_errors(monkeypatch):
    def fail_fetch(config):
        raise sync.SyncError("SSH unavailable")

    monkeypatch.setattr(sync, "_fetch_marker", fail_fetch)

    with pytest.raises(sync.SyncError, match="SSH unavailable"):
        sync._check_push_guard(_cfg(), force=False)


def test_push_uses_environment_host(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("providers: {}\n", encoding="utf-8")
    monkeypatch.setattr(sync, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sync, "_check_push_guard", lambda config, force: None)
    monkeypatch.setattr(
        sync,
        "_run_ssh_to_file",
        lambda config, remote_cmd, stdin_path, stdout_path: None,
    )
    monkeypatch.setenv("ALFRED_SYNC_HOST", "user@env-host")
    messages = []
    monkeypatch.setattr(
        sync, "console", SimpleNamespace(print=lambda message: messages.append(message))
    )

    sync.push(_cfg(host=""))

    assert any("user@env-host" in message for message in messages)
    assert (tmp_path / sync._MARKER).exists()


def test_tar_filter_excludes_locks_and_caches():
    for name in ("data/__pycache__/x.pyc", "data/.pytest_cache/x", "data/.lock"):
        assert sync._tar_filter(sync.tarfile.TarInfo(name)) is None

    item = sync.tarfile.TarInfo("data/memory/human.md")
    assert sync._tar_filter(item) is item
