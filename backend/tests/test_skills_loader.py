"""
T040: Skills 加载 — SKILL.md 解析 + 热更新 + prompt 注入

对标:
  - SKL-001: SKILL.md 格式 (YAML frontmatter + Markdown 正文, 多文件)
  - SKL-002: 热更新 (文件改动 → 缓存失效, 不重启)
  - SKL-003: 注入 SQL 生成 prompt (静态段, 作为约束规则)
  - Claude Code: Skills memoize + 文件监听
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.services.skills_loader import Skill, SkillsLoader


def _make_skill_file(dir_path: Path, name: str, content: str) -> Path:
    """创建一个 SKILL.md 文件。"""
    skill_dir = dir_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    f = skill_dir / "SKILL.md"
    f.write_text(content)
    return f


class TestSkillParsing:
    """SKL-001: SKILL.md 解析 (frontmatter + 正文)。"""

    def test_parse_frontmatter(self, tmp_path):
        f = _make_skill_file(tmp_path, "sql-rules", """\
---
name: sql-business-rules
description: SQL 生成的业务规则
version: 1.0
---

## 时间字段规则
使用 created_at 过滤时间
""")
        skill = Skill.from_file(f)
        assert skill.name == "sql-business-rules"
        assert skill.description == "SQL 生成的业务规则"
        assert "时间字段规则" in skill.content
        assert "created_at" in skill.content

    def test_parse_no_frontmatter(self, tmp_path):
        """无 frontmatter 的 SKILL.md → name 用目录名, 正文全部当 content。"""
        f = _make_skill_file(tmp_path, "basic", "Just some rules\nline 2")
        skill = Skill.from_file(f)
        assert "rules" in skill.content

    def test_parse_empty_file(self, tmp_path):
        f = _make_skill_file(tmp_path, "empty", "")
        skill = Skill.from_file(f)
        assert skill.content == ""


class TestSkillsLoader:
    """SKL-002: 加载多个 Skills + 热更新。"""

    def test_load_all_skills(self, tmp_path):
        _make_skill_file(tmp_path, "sql-rules", "---\nname: sql\ndescription: SQL规则\n---\nSQL规则内容")
        _make_skill_file(tmp_path, "chart-rules", "---\nname: chart\ndescription: 图表规则\n---\n图表规则内容")
        loader = SkillsLoader(base_dir=str(tmp_path))
        skills = loader.load_all()
        assert len(skills) == 2
        names = [s.name for s in skills]
        assert "sql" in names
        assert "chart" in names

    def test_empty_dir_returns_empty(self, tmp_path):
        loader = SkillsLoader(base_dir=str(tmp_path))
        assert loader.load_all() == []

    def test_hot_reload_detects_change(self, tmp_path):
        """SKL-002: 文件改动 → 缓存失效, 重新加载新内容。"""
        f = _make_skill_file(tmp_path, "rules", "---\nname: r\ndescription: v1\n---\n规则v1")
        loader = SkillsLoader(base_dir=str(tmp_path))
        skills1 = loader.load_all()
        assert any("v1" in s.content for s in skills1)

        # 修改文件
        time.sleep(0.1)
        f.write_text("---\nname: r\ndescription: v2\n---\n规则v2")
        loader.invalidate()  # 手动失效 (对标热更新)
        skills2 = loader.load_all()
        assert any("v2" in s.content for s in skills2)

    def test_format_for_prompt(self, tmp_path):
        """SKL-003: 格式化注入 prompt (作为约束规则)。"""
        _make_skill_file(tmp_path, "sql", "---\nname: sql\ndescription: SQL规则\n---\nGMV = SUM(total_amount)")
        loader = SkillsLoader(base_dir=str(tmp_path))
        text = loader.format_for_prompt()
        assert "GMV" in text
        assert "SUM(total_amount)" in text

    def test_empty_skills_empty_prompt(self, tmp_path):
        loader = SkillsLoader(base_dir=str(tmp_path))
        assert loader.format_for_prompt() == ""
