"""
T037: Relevant Recall — 按需召回 Agent 记忆注入 prompt

对标:
  - Claude Code §5.4: scanMemoryFiles → formatManifest → 轻量选择
  - config.memory_max_recall_count = 5
  - 宁缺毋滥: 无相关记忆返回空, 不灌无关内容

设计:
  - recall_memories(question, max_count): 从 agent_memory 的记忆清单里
    按关键词相关性选最多 max_count 条, 读取内容
  - 不调 LLM (省调用), 用关键词重叠度排序
  - format_memories_for_prompt: 格式化注入 SQL 生成 prompt 的动态段
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def recall_memories(
    question: str,
    memory_dir: str = "memory",
    max_count: int | None = None,
) -> list[dict]:
    """按相关性召回记忆 (最多 max_count 条)。

    对标 Claude Code §5.4: 不全量灌入, 只召回相关的。
    策略: 记忆 description 与问题的关键词重叠度 (轻量, 不调 LLM)。

    Args:
        question: 用户问题
        memory_dir: 记忆目录
        max_count: 最多返回条数 (None → config.memory_max_recall_count)

    Returns:
        [{name, description, content}, ...] 按相关性降序, 无匹配返回空
    """
    if max_count is None:
        from app.core.config import get_settings
        max_count = get_settings().memory_max_recall_count

    mem_path = Path(memory_dir)
    if not mem_path.exists():
        return []

    # 扫描记忆清单 (name + description)
    memories_meta = []
    for md_file in sorted(mem_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            # 提取 frontmatter 的 name + description
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            if name_match:
                memories_meta.append({
                    "name": name_match.group(1).strip(),
                    "description": desc_match.group(1).strip() if desc_match else "",
                    "content": content,
                    "file": str(md_file),
                })
        except Exception:
            continue

    if not memories_meta:
        return []

    # 关键词相关性排序 (description + name 与问题的词重叠)
    question_words = set(re.findall(r"[\w\u4e00-\u9fff]+", question.lower()))
    scored = []
    for mem in memories_meta:
        mem_words = set(re.findall(r"[\w\u4e00-\u9fff]+", (mem["name"] + " " + mem["description"]).lower()))
        overlap = len(question_words & mem_words)
        if overlap > 0:
            scored.append((overlap, mem))

    if not scored:
        return []  # 无相关记忆, 宁缺毋滥

    # 按重叠度降序, 取前 max_count
    scored.sort(key=lambda x: x[0], reverse=True)
    return [mem for _, mem in scored[:max_count]]


def format_memories_for_prompt(memories: list[dict]) -> str:
    """格式化记忆为 prompt 片段 (注入 SQL 生成动态段)。

    对标 Claude Code: formatMemoryManifest — 给模型约束性提示。
    """
    if not memories:
        return ""

    lines = ["【Agent 记忆 (相关业务知识)】"]
    for mem in memories:
        lines.append(f"[{mem['name']}] {mem['description']}")
        # 只注入摘要部分 (frontmatter 后的内容, 不含 frontmatter 本身)
        content = mem.get("content", "")
        body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL).strip()
        if body:
            lines.append(body[:200])  # 截断防 prompt 爆炸
    return "\n".join(lines)


def save_query_memory(question: str, tables: list[str], memory_dir: str = "memory") -> None:
    """记录用户查询到 memory (MEM-01 自主记忆写入闭环)。

    NOTE: 此函数目前无生产调用方 (chat.py/chat_stream.py 已移除调用)。
    与 fewshot 回流功能重叠 (fewshot 更有价值: 含 SQL, 语义检索)。
    保留作为备用, 若后续需要关键词记忆召回可重新接入。

    Args:
        question: 用户问题
        tables: 本轮查询涉及的表名
    """
    if not question:
        return
    try:
        from datetime import datetime
        mem_path = Path(memory_dir) / "recent_queries.md"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        # 文件不存在则创建 (带 frontmatter)
        if not mem_path.exists():
            mem_path.write_text(
                "---\nname: recent_queries\ndescription: 用户最近的查询记录和常用表\n---\n\n",
                encoding="utf-8",
            )
        # 追加一行 (时间 + 问题 + 表)
        ts = datetime.now().strftime("%m-%d %H:%M")
        tables_str = ", ".join(tables) if tables else "-"
        entry = f"- [{ts}] {question} (表: {tables_str})\n"
        with open(mem_path, "a", encoding="utf-8") as f:
            f.write(entry)
        # 防膨胀: 超过 200 行只保留最近 100 行
        lines = mem_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 200:
            # 保留 frontmatter (前几行) + 最近 100 行
            fm_end = 0
            for i, line in enumerate(lines):
                if line.strip() == "---" and i > 0:
                    fm_end = i + 1
                    break
            kept = lines[:fm_end] + lines[-100:]
            mem_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except Exception as e:
        logger.debug("save_query_memory 失败 (不阻塞): %s", e)
