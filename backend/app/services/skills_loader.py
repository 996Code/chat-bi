"""
T040: Skills 加载 — SKILL.md 解析 + 热更新 + prompt 注入

对标:
  - SKL-001: SKILL.md 格式 (YAML frontmatter + Markdown 正文, 多文件)
  - SKL-002: 热更新 (文件改动 → 缓存失效, 不重启)
  - SKL-003: 注入 SQL 生成 prompt (静态段, 作为约束规则)
  - Claude Code: Skills memoize + 文件监听

设计:
  - Skill: 单个 SKILL.md 解析结果 (name/description/content)
  - SkillsLoader: 扫描目录加载所有 SKILL.md, 带缓存 + invalidate
  - format_for_prompt: 拼接所有 Skills 为约束规则文本
  - 目录结构: skills/<name>/SKILL.md (如 skills/sql-rules/SKILL.md)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """一个 SKILL.md 的解析结果 (对标 SKL-001)。"""
    name: str = ""
    description: str = ""
    version: str = ""
    content: str = ""  # Markdown 正文 (业务规则)
    file_path: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "Skill":
        """解析 SKILL.md (YAML frontmatter + Markdown 正文)。"""
        text = path.read_text(encoding="utf-8")
        name = path.parent.name  # 默认 name = 目录名
        description = ""
        version = ""
        content = text

        # 提取 frontmatter (--- ... ---)
        fm_match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
        if fm_match:
            frontmatter = fm_match.group(1)
            content = fm_match.group(2).strip()
            # 简单解析 YAML frontmatter (不用 PyYAML, 只取 key: value)
            for line in frontmatter.split("\n"):
                m = re.match(r"^(\w+):\s*(.+)$", line)
                if m:
                    key, val = m.group(1), m.group(2).strip()
                    if key == "name":
                        name = val
                    elif key == "description":
                        description = val
                    elif key == "version":
                        version = val

        return cls(name=name, description=description, version=version,
                   content=content, file_path=str(path))


class SkillsLoader:
    """Skills 加载器 (对标 SKL-002 热更新)。

    扫描 base_dir 下的所有 <name>/SKILL.md。
    带缓存: invalidate() 后下次 load_all 重新读取文件。
    """

    def __init__(self, base_dir: str = "skills"):
        self._base_dir = Path(base_dir)
        self._cache: list[Skill] | None = None

    def load_all(self) -> list[Skill]:
        """加载所有 SKILL.md (带缓存, invalidate 后重新加载)。"""
        if self._cache is not None:
            return self._cache

        skills: list[Skill] = []
        if not self._base_dir.exists():
            self._cache = skills
            return skills

        for skill_file in sorted(self._base_dir.glob("*/SKILL.md")):
            try:
                skill = Skill.from_file(skill_file)
                if skill.content:  # 空内容的不加载
                    skills.append(skill)
            except Exception as e:
                logger.warning("Skill 加载失败 %s: %s", skill_file, e)

        self._cache = skills
        logger.info("Skills 加载: %d 个", len(skills))
        return skills

    def invalidate(self) -> None:
        """缓存失效 (对标 SKL-002 热更新: 文件改动后调此方法)。"""
        self._cache = None

    def format_for_prompt(self) -> str:
        """格式化所有 Skills 为 prompt 注入文本 (对标 SKL-003)。

        作为约束规则注入 SQL 生成 prompt 静态段。
        """
        skills = self.load_all()
        if not skills:
            return ""

        lines = ["【业务规则 (Skills) — 必须严格遵循】"]
        for skill in skills:
            if skill.description:
                lines.append(f"### {skill.description}")
            lines.append(skill.content)
        return "\n".join(lines)


# 模块级单例
_skills_loader: SkillsLoader | None = None


def get_skills_loader(base_dir: str | None = None) -> SkillsLoader:
    """获取全局 SkillsLoader 单例。"""
    global _skills_loader
    if _skills_loader is not None and base_dir is None:
        return _skills_loader
    from app.core.config import get_settings
    _skills_loader = SkillsLoader(base_dir or getattr(get_settings(), "skills_dir", "skills"))
    return _skills_loader


def reset_skills_loader() -> None:
    """重置单例 (测试用)。"""
    global _skills_loader
    _skills_loader = None
