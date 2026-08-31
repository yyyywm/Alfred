"""skills / rules 加载器测试。"""

from alfred.config import Config
from alfred.rules.loader import render_rules, scan_rules
from alfred.skills.loader import render_skills_index, scan_skills

SKILL_MD = """---
name: test-skill
description: 测试用技能
---

# 正文
"""

RULE_ALWAYS = """---
description: 总是生效的规则
alwaysApply: true
---

规则正文
"""

RULE_RECALL = """---
description: 按需召回的规则
---

另一条规则
"""


def _config(tmp_path, skills_dir=None, rules_dir=None):
    return Config(paths={
        "skills_dirs": [str(skills_dir or tmp_path / "skills")],
        "rules_dirs": [str(rules_dir or tmp_path / "rules")],
    })


def test_scan_skills(tmp_path):
    d = tmp_path / "skills" / "test-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    skills = scan_skills(_config(tmp_path))
    assert len(skills) == 1
    assert skills[0].name == "test-skill"
    index = render_skills_index(skills)
    assert "test-skill" in index and "SKILL.md" in index


def test_skill_without_description_skipped(tmp_path):
    d = tmp_path / "skills" / "bad"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: bad\n---\n正文\n", encoding="utf-8")
    assert scan_skills(_config(tmp_path)) == []


def test_project_skills_discovered():
    """用户级 skills 目录（~/.agents/skills）应能被发现并加载内置 skill。

    注意：不断言具体技能名——技能集随用户安装/删除而变化，硬编码技能名会让
    测试与真实环境脱节（此前因断言已不存在的 software-dev-workflow 而误报失败）。
    这里只验证目录能被扫描、技能元数据完整。
    """
    skills = scan_skills(Config())
    # 用户环境至少应装有一些技能
    assert len(skills) > 0
    # 每个技能都有合法名字
    assert all(s.name for s in skills)


def test_skill_when_to_use_in_index(tmp_path):
    d = tmp_path / "skills" / "wtu-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: wtu-skill\ndescription: 一个技能\nwhen-to-use: 当用户明确要求时使用\n---\n正文\n",
        encoding="utf-8",
    )
    skills = scan_skills(_config(tmp_path))
    assert skills[0].when_to_use == "当用户明确要求时使用"
    index = render_skills_index(skills)
    assert "触发：" in index
    assert "当用户明确要求时使用" in index


def test_skill_disable_model_invocation(tmp_path):
    d = tmp_path / "skills" / "user-only"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: user-only\ndescription: 只能用户手动触发\ndisable-model-invocation: true\n---\n正文\n",
        encoding="utf-8",
    )
    skills = scan_skills(_config(tmp_path))
    assert skills[0].disable_model_invocation is True
    index = render_skills_index(skills)
    assert "不得自行调用" in index
    assert "user-only" in index


def test_rules_four_triggers(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "always.md").write_text(RULE_ALWAYS, encoding="utf-8")
    (d / "recall.md").write_text(RULE_RECALL, encoding="utf-8")
    rules = scan_rules(_config(tmp_path))
    always_text, index_text = render_rules(rules)
    assert "规则正文" in always_text          # alwaysApply 进常驻
    assert "另一条规则" not in always_text    # 非常驻正文不注入
    assert "recall" in index_text             # 但索引里有


def test_project_rules_discovered():
    rules = scan_rules(Config())
    assert any(r.always_apply for r in rules), "communication-style 应为常驻规则"
