"""
T040: Skills 加载 — SKILL.md 解析 + 热更新 + prompt 注入
T060: Skills 分层子目录 — reference/*.md 按数据源类型自动加载

对标:
  - SKL-001: SKILL.md 格式 (YAML frontmatter + Markdown 正文, 多文件)
  - SKL-002: 热更新 (文件改动 → 缓存失效, 不重启)
  - SKL-003: 注入 SQL 生成 prompt (静态段, 作为约束规则)
  - Claude Code: Skills memoize + 文件监听

设计:
  - Skill: 单个 SKILL.md 解析结果 (name/description/content/references)
  - SkillsLoader: 扫描目录加载所有 SKILL.md + reference/*.md, 带缓存 + invalidate
  - format_for_prompt: 拼接所有 Skills 为约束规则文本, 按 db_type 自动加载对应 reference
  - 目录结构:
      skills/<name>/SKILL.md              # 通用规则
      skills/<name>/reference/mysql.md     # MySQL 方言规则
      skills/<name>/reference/postgresql.md # PostgreSQL 方言规则
      skills/<name>/reference/bar.md       # 柱状图规则
      skills/<name>/reference/line.md      # 折线图规则
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """一个 SKILL.md 的解析结果 (对标 SKL-001)。

    name: 技能名称, 取自 frontmatter 或目录名
    description: 技能描述, 取自 frontmatter
    version: 技能版本, 取自 frontmatter
    content: Markdown 正文 (业务规则), 不含 frontmatter
    file_path: SKILL.md 的绝对路径, 用于调试和日志
    references: T060 reference 子文件, key=文件名(不含.md), value=文件内容

    解析流程:
    SKILL.md → 读取全部文本 → 提取 frontmatter (--- ... ---)
    → 提取正文 → 加载 reference/*.md → 构造 Skill 对象
    """
    name: str = ""
    description: str = ""
    version: str = ""
    content: str = ""  # Markdown 正文 (业务规则)
    file_path: str = ""
    # T060: reference 子文件 — key 为文件名(不含 .md), value 为内容
    references: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> "Skill":
        """解析 SKILL.md (YAML frontmatter + Markdown 正文) + reference/*.md。

        为什么不用 PyYAML:
        - frontmatter 格式简单 (只有 key: value), 手写正则足够
        - 避免引入 PyYAML 依赖 (SKILL.md 是内部格式, 非标准 YAML)
        - 如果 future 需要复杂 YAML 结构, 可切换为 PyYAML

        为什么 reference 文件按文件名排序加载:
        - 确保加载顺序稳定 (文件系统迭代顺序不一定)
        - 排序后 reference 按字母顺序注入 prompt, 便于调试
        """
        text = path.read_text(encoding="utf-8")
        name = path.parent.name  # 默认 name = 目录名
        description = ""
        version = ""
        content = text

        # 提取 frontmatter (--- ... ---)
        # 格式: 文件开头 ---\nkey: value\n...\n---\n正文
        # 正则中的 re.DOTALL 让 . 匹配换行符
        fm_match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
        if fm_match:
            frontmatter = fm_match.group(1)
            content = fm_match.group(2).strip()
            # 简单解析 YAML frontmatter (不用 PyYAML, 只取 key: value)
            # 每行格式: key: value (key 只能是字母数字下划线)
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

        # T060: 加载 reference/*.md 子文件
        # reference 目录结构:
        #   skills/<name>/reference/mysql.md
        #   skills/<name>/reference/postgresql.md
        #   skills/<name>/reference/bar.md
        #   skills/<name>/reference/line.md
        # 文件名 (不含 .md) 作为 reference key, 用于按 db_type 匹配
        references: dict[str, str] = {}
        ref_dir = path.parent / "reference"
        if ref_dir.is_dir():
            # 按文件名排序, 确保加载顺序稳定
            for ref_file in sorted(ref_dir.glob("*.md")):
                ref_key = ref_file.stem  # mysql, postgresql, bar, line ...
                try:
                    references[ref_key] = ref_file.read_text(encoding="utf-8").strip()
                except Exception as e:
                    # 单个 reference 文件加载失败不影响其他文件
                    logger.warning("Skill reference 加载失败 %s: %s", ref_file, e)

        return cls(name=name, description=description, version=version,
                   content=content, file_path=str(path), references=references)


class SkillsLoader:
    """Skills 加载器 (对标 SKL-002 热更新 + T060 分层子目录)。

    扫描 base_dir 下的所有 <name>/SKILL.md 及 <name>/reference/*.md。
    带缓存: invalidate() 后下次 load_all 重新读取文件。
    首次加载空目录时自动从 _template/ 复制种子规则。

    热更新机制:
    - 每次 load_all 调用时, 比对目录 mtime
    - 如果 mtime 变化, 自动失效缓存并重新加载
    - 支持 admin HTTP 编辑和直接文件编辑两种场景
    - mtime 粒度为秒级, 文件修改后立即生效

    缓存策略:
    - 缓存为 list[Skill], 无 TTL 过期
    - 完全依赖 mtime 检测失效
    - 加载/解析开销小 (几十个文件), 不需要更复杂的缓存策略
    """

    def __init__(self, base_dir: str = "skills"):
        self._base_dir = Path(base_dir)
        self._cache: list[Skill] | None = None
        # 热更新: 记录上次加载时的目录 mtime, 变化则自动失效缓存重载
        # T060: mtime 检测范围包含 reference/ 子目录
        self._last_mtime: float = 0.0

    def _current_dir_mtime(self) -> float:
        """取 skills 目录下所有 SKILL.md + reference/*.md 的最新 mtime。

        如果目录不存在, 返回 0.0 (表示需要重新加载)。
        如果文件系统异常, 返回 0.0 (安全降级, 避免缓存无法失效)。

        注意: 只检测 SKILL.md 和 reference/*.md, 不检测其他文件。
        因为其他文件的变化不影响 skills 加载结果。
        """
        if not self._base_dir.exists():
            return 0.0
        try:
            mtimes = [f.stat().st_mtime for f in self._base_dir.glob("*/SKILL.md")]
            # T060: 也监控 reference/ 子文件
            mtimes.extend(f.stat().st_mtime for f in self._base_dir.glob("*/reference/*.md"))
            return max(mtimes)
        except (ValueError, OSError):
            # 文件系统错误: 返回 0.0 触发重新加载, 而非让缓存永久失效
            return 0.0

    def load_all(self) -> list[Skill]:
        """加载所有 SKILL.md + reference/*.md (带缓存 + mtime 热更新)。

        热更新 (对标 SKL-002): 每次调用比对目录 mtime, 文件改动自动重载,
        无需重启也无需手动 invalidate (admin HTTP 编辑 / 直接改文件都生效)。
        首次加载空目录时自动从 _template/ 复制种子规则。

        每次调用都检查 mtime, 不是每次调用都重新加载文件。
        缓存命中时直接返回, 绕过文件读取和解析。
        """
        # mtime 变化 → 文件被改过, 失效缓存
        current_mtime = self._current_dir_mtime()
        if current_mtime != self._last_mtime:
            self._cache = None
            self._last_mtime = current_mtime

        if self._cache is not None:
            return self._cache

        skills: list[Skill] = []
        if not self._base_dir.exists():
            self._base_dir.mkdir(parents=True, exist_ok=True)

        # 种子数据: 目录为空时从 _template/ 复制默认规则
        self._ensure_seed_skills()

        # 按文件名排序加载, 确保加载顺序稳定
        # 顺序影响 prompt 中的 Skills 顺序, 排序后可预测
        for skill_file in sorted(self._base_dir.glob("*/SKILL.md")):
            try:
                skill = Skill.from_file(skill_file)
                if skill.content:  # 空内容的不加载
                    skills.append(skill)
            except Exception as e:
                # 单个文件加载失败不影响其他文件
                logger.warning("Skill 加载失败 %s: %s", skill_file, e)

        self._cache = skills
        ref_count = sum(len(s.references) for s in skills)
        logger.info("Skills 加载: %d 个, %d 个 reference 文件", len(skills), ref_count)
        return skills

    def invalidate(self) -> None:
        """缓存失效 (对标 SKL-002 热更新: 文件改动后调此方法)。

        手动失效: 适用于文件修改后需要立即生效的场景。
        自动失效: 通过 mtime 检测, 下次 load_all 自动重载。
        两者互补: 自动失效覆盖大部分场景, 手动失效用于强制刷新。
        """
        self._cache = None

    def _ensure_seed_skills(self) -> None:
        """目录为空时从 _template/ 复制种子规则 (不覆盖已有内容)。

        种子规则包含电商场景常见业务约定 (GMV/状态枚举/字段别名),
        用户可自由编辑或删除, 不影响其他租户。

        只在首次部署时 (目录为空) 复制种子规则。
        如果用户已创建自定义规则, 不覆盖。
        如果 _template 目录不存在, 跳过 (正常行为, 不是错误)。
        """
        import shutil
        # 已有规则 → 不覆盖
        # 检查是否有 SKILL.md 文件, 有则说明已有规则
        if any(self._base_dir.glob("*/SKILL.md")):
            return
        template_dir = self._base_dir.parent / "_template"
        if not template_dir.is_dir():
            return
        try:
            for item in template_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    dest = self._base_dir / item.name
                    if not dest.exists():
                        shutil.copytree(item, dest)
            logger.info("种子 Skills 已初始化到 %s", self._base_dir)
        except Exception as e:
            # 种子初始化失败不阻塞, 用户可以手动创建规则
            logger.warning("种子 Skills 初始化失败 (不阻塞): %s", e)

    def format_for_prompt(self, db_type: str | None = None) -> str:
        """格式化所有 Skills 为 prompt 注入文本 (对标 SKL-003 + T060)。

        作为约束规则注入 SQL 生成 prompt 静态段。
        T060: db_type 匹配时自动拼接对应的 reference/*.md 内容。

        输出格式:
        【业务规则 (Skills) — 必须严格遵循】
        ### description
        content
        ### mysql 方言规则
        ref_content (if db_type matches)

        Args:
            db_type: 数据源类型 (mysql/postgresql), 匹配 reference/<db_type>.md

        注意: 返回的文本是 markdown 格式, 直接嵌入 LLM prompt。
        如果没有任何 skills, 返回空字符串 (调用方不拼接空文本)。
        """
        skills = self.load_all()
        if not skills:
            return ""

        lines = ["【业务规则 (Skills) — 必须严格遵循】"]
        for skill in skills:
            if skill.description:
                lines.append(f"### {skill.description}")
            lines.append(skill.content)

            # T060: 按 db_type 注入对应 reference
            # 如果 skill 有 references 且匹配 db_type, 注入方言规则
            if db_type and skill.references:
                ref_content = skill.references.get(db_type)
                if ref_content:
                    lines.append(f"### {db_type} 方言规则")
                    lines.append(ref_content)

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
