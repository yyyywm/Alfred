"""数据备份：把全部运行时数据与配置打包成 zip，供迁移到其他机器。

备份内容：
- 项目内：config.yaml / .env / rules/ / data/ / hist/（存在才打包）
- 项目外：config.paths.skills_dirs 与 rules_dirs 中解析到项目根之外的目录
  （如 ~/.agents/skills），存到 external/<kind>-<i>/ 下，附 .target 记录原始路径
- RESTORE.md：恢复步骤说明

排除：__pycache__、.pytest_cache、.lock（Qdrant 残留锁，带过去反而导致
mem0 初始化失败，见 AGENTS.md 常见坑点）。

注意：备份包含 .env（API key 明文），zip 文件需自行妥善保管。
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, Config

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {".lock"}

# 项目内需要打包的路径（相对项目根，不存在则跳过）
PROJECT_ITEMS = ["config.yaml", ".env", "rules", "data", "hist"]


def _resolve(p: str, project_root: Path) -> Path:
    expanded = Path(os.path.expanduser(p))
    return expanded if expanded.is_absolute() else project_root / expanded


def _add_dir(z: zipfile.ZipFile, src: Path, prefix: str) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn in EXCLUDE_FILES:
                continue
            fp = Path(dirpath) / fn
            arc = f"{prefix}/{fp.relative_to(src)}".replace("\\", "/")
            z.write(fp, arc)
            count += 1
    return count


def _restore_md(date: str, externals: list[tuple[str, Path, str]]) -> str:
    lines = [
        f"# Alfred 备份恢复说明（备份日期：{date}）",
        "",
        "## 备份内容",
        "- `project/`：项目根下的 config.yaml、.env、rules/、data/、hist/",
        "  - `data/` 内含记忆 git 仓库（human/persona/lessons）、会话历史、",
        "    LanceDB/Qdrant 向量库——向量库随包迁移，无需重建索引",
    ]
    for arc_prefix, _resolved, original in externals:
        lines.append(f"- `{arc_prefix}/`：应恢复到 `{original}`")
    lines += [
        "",
        "## 新电脑恢复步骤",
        "1. 克隆/复制 Alfred 源码到新电脑",
        "2. 安装环境：`conda env create -f environment.yml && conda activate alfred`"
        "（或 `pip install -e \".[dev]\"`）",
        "3. 把 `project/` 下的内容覆盖到项目根目录",
        "4. 把 `external/` 下各目录恢复到其 `.target` 文件标注的原始路径",
        "5. 验证：`alfred models` → `alfred memory list` → `alfred chat`",
        "",
        "提示：embedding 模型首次运行会重新下载（约 600MB）。",
        "",
        "警告：本备份包含 .env（API key 明文），请妥善保管此 zip 文件。",
    ]
    return "\n".join(lines) + "\n"


def create_backup(
    config: Config,
    output: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> tuple[Path, int]:
    """打包备份，返回 (zip 路径, 文件数)。"""
    if output is None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = project_root / "backups" / f"alfred-backup-{ts}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)

    # 收集项目外的 skills/rules 目录：(zip 内前缀, 解析后路径, 原始配置字符串)
    externals: list[tuple[str, Path, str]] = []
    for kind, dirs in (("skills", config.paths.skills_dirs),
                       ("rules", config.paths.rules_dirs)):
        for i, d in enumerate(dirs):
            resolved = _resolve(d, project_root)
            if resolved.is_relative_to(project_root) or not resolved.is_dir():
                continue  # 项目内目录已被 PROJECT_ITEMS 覆盖
            externals.append((f"external/{kind}-{i}", resolved, d))

    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "RESTORE.md",
            _restore_md(datetime.now().strftime("%Y-%m-%d"), externals),
        )
        for item in PROJECT_ITEMS:
            p = project_root / item
            if p.is_file():
                z.write(p, f"project/{item}")
                count += 1
            elif p.is_dir():
                count += _add_dir(z, p, f"project/{item}")
        for arc_prefix, resolved, original in externals:
            z.writestr(f"{arc_prefix}/.target", original + "\n")
            count += _add_dir(z, resolved, arc_prefix)

    return output, count
