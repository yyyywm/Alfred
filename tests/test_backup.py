"""备份打包测试（纯逻辑，用临时目录，不碰真实数据与 skills 目录）。"""

import zipfile
from pathlib import Path

from alfred.backup import create_backup
from alfred.config import Config, PathsConfig


def _make_project(root: Path) -> None:
    (root / "data" / "memory").mkdir(parents=True)
    (root / "data" / "memory" / "human.md").write_text("# human", encoding="utf-8")
    qdrant = root / "data" / "vectordb" / "qdrant_mem0"
    qdrant.mkdir(parents=True)
    (qdrant / ".lock").write_text("", encoding="utf-8")
    (qdrant / "data.bin").write_text("1", encoding="utf-8")
    cache = root / "data" / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_text("", encoding="utf-8")
    (root / "config.yaml").write_text("providers: {}", encoding="utf-8")
    (root / ".env").write_text("KEY=1", encoding="utf-8")


def test_backup_includes_project_data_and_env(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    skills = tmp_path / "my-skills"
    (skills / "s").mkdir(parents=True)
    (skills / "s" / "SKILL.md").write_text("x", encoding="utf-8")

    cfg = Config(paths=PathsConfig(skills_dirs=[str(skills)], rules_dirs=[]))
    out = tmp_path / "out.zip"
    path, count = create_backup(cfg, out, project_root=proj)

    assert path == out and out.exists() and count > 0
    names = zipfile.ZipFile(out).namelist()
    assert "project/config.yaml" in names
    assert "project/.env" in names
    assert "project/data/memory/human.md" in names
    assert "project/data/vectordb/qdrant_mem0/data.bin" in names
    assert "RESTORE.md" in names
    assert any(n.startswith("external/skills-0/") for n in names)
    assert f"external/skills-0/.target" in names


def test_backup_excludes_lock_and_pycache(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    cfg = Config(paths=PathsConfig(skills_dirs=[], rules_dirs=[]))
    out = tmp_path / "out.zip"
    create_backup(cfg, out, project_root=proj)
    names = zipfile.ZipFile(out).namelist()
    assert not any(n.endswith(".lock") for n in names)
    assert not any("__pycache__" in n for n in names)


def test_backup_default_output_under_backups_dir(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    cfg = Config(paths=PathsConfig(skills_dirs=[], rules_dirs=[]))
    path, _ = create_backup(cfg, None, project_root=proj)
    assert path.parent == proj / "backups"
    assert path.name.startswith("alfred-backup-") and path.suffix == ".zip"


def test_backup_skips_project_internal_rules_dir(tmp_path):
    """rules_dirs 中解析到项目根内的目录不应重复进 external/。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    (proj / "rules").mkdir()
    (proj / "rules" / "r.md").write_text("x", encoding="utf-8")
    cfg = Config(paths=PathsConfig(skills_dirs=[], rules_dirs=["rules"]))
    out = tmp_path / "out.zip"
    create_backup(cfg, out, project_root=proj)
    names = zipfile.ZipFile(out).namelist()
    assert "project/rules/r.md" in names
    assert not any(n.startswith("external/rules") for n in names)
