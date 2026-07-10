"""
ChatBI v2 — Agent Memory: File-based storage + MEMORY.md index (T010)

Each memory = one .md file in the memory directory.
文件名 = UUID (不可变主键), frontmatter 存 name(可编辑标题) + description + type + consolidated.
MEMORY.md maintains the index (links + one-line descriptions).

对标: Claude Code memdir — MEMORY.md 索引 + topic memories/*.md
      Relevant Recall: scan memory files → format manifest → select top 5
"""
from __future__ import annotations

import logging
import re
import uuid as uuid_mod
from pathlib import Path
from typing import Optional

FIRST_SECTION_PATTERN = re.compile(r"^#+\s", re.MULTILINE)

logger = logging.getLogger(__name__)


class AgentMemoryStore:
    """
    File-based agent memory storage.

    Directory structure:
      memory/
      ├── MEMORY.md            ← index (links + one-line descriptions)
      ├── {uuid}.md            ← one memory file per fact (filename = immutable id)

    Each memory file has YAML frontmatter:
      ---
      id: {uuid}                ← immutable primary key (= filename)
      name: <human-readable title, editable>
      description: <one-line summary for recall>
      metadata:
        type: user | feedback | project | reference
        consolidated: false     ← true if this memory has been merged into a consolidated one
      ---
    """

    def __init__(self, base_dir: str = "memory"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "MEMORY.md"
        # 种子数据: 目录为空时从 _template/ 复制默认记忆
        self._ensure_seed_memories()

    def _init_index(self) -> None:
        """Create MEMORY.md index if it doesn't exist."""
        if not self.index_path.exists():
            self.index_path.write_text(
                "# Agent Memory Index\n\n"
                "This directory stores persistent agent memories.\n"
                "Each entry below links to a memory file.\n\n"
                "---\n\n",
                encoding="utf-8",
            )

    def _ensure_seed_memories(self) -> None:
        """目录为空时从 _template/ 复制种子记忆 (不覆盖已有内容)。

        种子记忆包含电商场景常见业务约定, 用户可自由编辑或删除。
        _template 在 memory/ 根目录下: memory/_template/。
        当前目录可能是 memory/{tenant_id}/{data_source_id}/,
        需要向上两级找到 memory/。
        """
        import shutil
        # 已有记忆 → 不覆盖 (排除 MEMORY.md 索引文件)
        if any(f for f in self.base_dir.glob("*.md") if f.name != "MEMORY.md"):
            return
        # 向上查找 _template: memory/{tenant_id}/{data_source_id}/ → memory/
        # 尝试当前目录的 parent (一层) 和 parent.parent (两层)
        template_dir = None
        for candidate in [self.base_dir.parent / "_template", self.base_dir.parent.parent / "_template"]:
            if candidate.is_dir():
                template_dir = candidate
                break
        if not template_dir:
            return
        try:
            for item in template_dir.iterdir():
                if item.is_file() and item.suffix == ".md" and item.name != "MEMORY.md":
                    dest = self.base_dir / item.name
                    if not dest.exists():
                        shutil.copy2(item, dest)
            # 更新索引: 为每个种子记忆添加链接
            self._rebuild_index_from_files()
            logger.info("种子 Memory 已初始化到 %s", self.base_dir)
        except Exception as e:
            logger.warning("种子 Memory 初始化失败 (不阻塞): %s", e)

    def _rebuild_index_from_files(self) -> None:
        """根据目录中的 .md 文件重建 MEMORY.md 索引。"""
        self._init_index()
        for md_file in sorted(self.base_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            head = md_file.read_text(encoding="utf-8")[:500]
            desc_match = re.search(r"^description:\s*(.+)$", head, re.MULTILINE)
            name_match = re.search(r"^name:\s*(.+)$", head, re.MULTILINE)
            name = name_match.group(1).strip() if name_match else md_file.stem
            description = desc_match.group(1).strip() if desc_match else name
            self._update_index_link(md_file.name, description)

    def read_index(self) -> str:
        """Read the MEMORY.md index content."""
        self._init_index()
        content = self.index_path.read_text(encoding="utf-8")
        # Truncate protection: 200 lines or 25KB max (对标 Claude Code)
        lines = content.split("\n")
        if len(lines) > 200:
            lines = lines[:200]
            content = "\n".join(lines)
        if len(content.encode("utf-8")) > 25_000:
            content = content[:25_000]
        return content

    def save_memory(
        self,
        name: str,
        description: str,
        content: str,
        memory_type: str = "project",
        mem_id: str | None = None,
    ) -> Path:
        """Save a memory file and update the index.

        Args:
            name: Human-readable title (editable)
            description: One-line summary for the index and recall
            content: The memory content (markdown body)
            memory_type: user | feedback | project | reference
            mem_id: Existing UUID to update (None = create new)

        Returns:
            Path to the created memory file.
        """
        self._init_index()

        # 写记忆文件
        if mem_id:
            # 更新已有记忆: filename = {mem_id}.md
            file_name = f"{mem_id}.md"
        else:
            # 新建: 生成 UUID
            mem_id = str(uuid_mod.uuid4())
            file_name = f"{mem_id}.md"
        file_path = self.base_dir / file_name

        frontmatter = (
            f"---\n"
            f"id: {mem_id}\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"metadata:\n"
            f"  type: {memory_type}\n"
            f"  consolidated: false\n"
            f"---\n\n"
            f"{content}\n"
        )
        file_path.write_text(frontmatter, encoding="utf-8")

        # Update index
        self._update_index_link(file_name, description)

        logger.info("Memory saved: %s (%s)", name, memory_type)
        return file_path

    def _update_index_link(self, file_name: str, description: str) -> None:
        """Add or update a link entry in MEMORY.md."""
        index_content = self.index_path.read_text(encoding="utf-8")
        link_line = f"- [{file_name.replace('.md', '')}]({file_name}) — {description}"

        # Replace existing entry if present
        existing_pattern = re.compile(
            rf"^- \[{re.escape(file_name.replace('.md', ''))}\]\(.*\) — .*$",
            re.MULTILINE,
        )
        if existing_pattern.search(index_content):
            index_content = existing_pattern.sub(link_line, index_content)
        else:
            index_content += f"{link_line}\n"

        self.index_path.write_text(index_content, encoding="utf-8")

    def delete_memory(self, mem_id: str) -> bool:
        """Delete a memory file and remove its index entry.

        Args:
            mem_id: UUID of the memory (also the filename stem)
        """
        # mem_id 可能是完整 UUID 或 filename, 取 stem
        stem = mem_id.replace(".md", "")
        file_path = self.base_dir / f"{stem}.md"
        if not file_path.exists():
            return False

        file_path.unlink()

        # Remove from index
        index_content = self.index_path.read_text(encoding="utf-8")
        existing_pattern = re.compile(
            rf"^- \[{re.escape(stem)}\]\(.*\) — .*$\n?",
            re.MULTILINE,
        )
        index_content = existing_pattern.sub("", index_content)
        self.index_path.write_text(index_content, encoding="utf-8")

        logger.info("Memory deleted: %s", stem)
        return True

    def list_memories(self) -> list[dict[str, str]]:
        """List all memory files with their metadata (for API + recall).

        对标 Claude Code: scanMemoryFiles() — scan file headers only,
        return id/name/description/type for the lightweight model to select.
        """
        memories = []
        for file_path in self.base_dir.glob("*.md"):
            if file_path.name == "MEMORY.md":
                continue

            # Read only the frontmatter (first 500 chars) for fast scanning
            head = file_path.read_text(encoding="utf-8")[:500]

            id_match = re.search(r"^id:\s*(.+)$", head, re.MULTILINE)
            name_match = re.search(r"^name:\s*(.+)$", head, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", head, re.MULTILINE)
            type_match = re.search(r"^\s*type:\s*(.+)$", head, re.MULTILINE)
            consolidated_match = re.search(r"^\s*consolidated:\s*(true|false)", head, re.MULTILINE)

            # id: 优先 frontmatter, 回退 filename (兼容旧格式无 id 的种子文件)
            mem_id = id_match.group(1).strip() if id_match else file_path.stem

            memories.append({
                "id": mem_id,
                "name": name_match.group(1).strip() if name_match else file_path.stem,
                "file": file_path.name,
                "description": desc_match.group(1).strip() if desc_match else "",
                "type": type_match.group(1).strip() if type_match else "project",
                "consolidated": consolidated_match.group(1).strip().lower() == "true" if consolidated_match else False,
                "path": str(file_path.absolute()),
            })

        return memories

    def mark_consolidated(self, mem_id: str) -> bool:
        """标记记忆为已整理 (在 frontmatter 加 consolidated: true)。

        整理后原始记忆不删除, 但默认隐藏, 用户可通过开关查看。
        """
        stem = mem_id.replace(".md", "")
        file_path = self.base_dir / f"{stem}.md"
        if not file_path.exists():
            return False
        try:
            content = file_path.read_text(encoding="utf-8")
            # 如果已有 consolidated 标记, 跳过
            if re.search(r"^\s*consolidated:\s*true", content, re.MULTILINE):
                return True
            original = content
            # 在 metadata 段加 consolidated: true (健壮: 先尝试 type 行后插入, 再尝试 metadata 行后插入)
            if "metadata:" in content:
                # 方式1: type 行后插入 (正常情况)
                content = re.sub(
                    r"(metadata:\n(\s+)type: .+\n)",
                    r"\1\2consolidated: true\n",
                    content,
                )
                # 方式1 没生效 (metadata 段无 type 行) → 方式2: metadata 行后直接插入
                if content == original:
                    content = re.sub(
                        r"(metadata:\n)",
                        r"\1  consolidated: true\n",
                        content,
                    )
            else:
                # 没有 metadata 段, 在 frontmatter 末尾 (第二个 --- 前) 加
                content = re.sub(
                    r"(^---\n.*?)(\n---\n)",
                    r"\1\nmetadata:\n  consolidated: true\2",
                    content,
                    count=1,
                    flags=re.DOTALL,
                )
            # 校验: 替换后必须包含 consolidated: true
            if "consolidated: true" not in content:
                logger.warning("mark_consolidated: 无法插入标记, frontmatter 可能格式异常: %s", stem)
                return False
            file_path.write_text(content, encoding="utf-8")
            logger.info("Memory marked consolidated: %s", stem)
            return True
        except Exception as e:
            logger.warning("mark_consolidated 失败: %s", e)
            return False

    def read_memory(self, mem_id: str) -> Optional[str]:
        """Read the full content of a specific memory file."""
        stem = mem_id.replace(".md", "")
        file_path = self.base_dir / f"{stem}.md"
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8")


# ── Global instance ────────────────────────────────────────────

_agent_memory_store: AgentMemoryStore | None = None


def get_agent_memory_store(base_dir: str | None = None) -> AgentMemoryStore:
    """Get or create the global agent memory store."""
    global _agent_memory_store
    if _agent_memory_store is None:
        _agent_memory_store = AgentMemoryStore(base_dir=base_dir or "memory")
    return _agent_memory_store
