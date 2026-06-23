"""
ChatBI v2 — Agent Memory: File-based storage + MEMORY.md index (T010)

Each memory = one .md file in the memory directory.
MEMORY.md maintains the index (links + one-line descriptions).

对标: Claude Code memdir — MEMORY.md 索引 + topic memories/*.md
      Relevant Recall: scan memory files → format manifest → select top 5
"""
from __future__ import annotations

import logging
import re
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
      ├── tenant-xxx.md         ← one memory file per fact
      └── preference-xxx.md

    Each memory file has YAML frontmatter:
      ---
      name: <slug>
      description: <one-line summary>
      metadata:
        type: user | feedback | project | reference
      ---
    """

    def __init__(self, base_dir: str = "memory"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "MEMORY.md"

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
    ) -> Path:
        """Save a memory file and update the index.

        Args:
            name: Short kebab-case slug for the file
            description: One-line summary for the index
            content: The memory content (markdown body)
            memory_type: user | feedback | project | reference

        Returns:
            Path to the created memory file.
        """
        self._init_index()

        # Write memory file
        file_name = f"{name}.md"
        file_path = self.base_dir / file_name

        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"metadata:\n"
            f"  type: {memory_type}\n"
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

    def delete_memory(self, name: str) -> bool:
        """Delete a memory file and remove its index entry."""
        file_name = f"{name}.md"
        file_path = self.base_dir / file_name

        if not file_path.exists():
            return False

        file_path.unlink()

        # Remove from index
        index_content = self.index_path.read_text(encoding="utf-8")
        existing_pattern = re.compile(
            rf"^- \[{re.escape(name)}\]\(.*\) — .*$\n?",
            re.MULTILINE,
        )
        index_content = existing_pattern.sub("", index_content)
        self.index_path.write_text(index_content, encoding="utf-8")

        logger.info("Memory deleted: %s", name)
        return True

    def list_memories(self) -> list[dict[str, str]]:
        """List all memory files with their descriptions (for relevant recall).

        对标 Claude Code: scanMemoryFiles() — scan file headers only,
        return file name + description for the lightweight model to select.
        """
        memories = []
        for file_path in self.base_dir.glob("*.md"):
            if file_path.name == "MEMORY.md":
                continue

            # Read only the frontmatter (first 500 chars) for fast scanning
            head = file_path.read_text(encoding="utf-8")[:500]

            name_match = re.search(r"^name:\s*(.+)$", head, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", head, re.MULTILINE)
            type_match = re.search(r"^\s*type:\s*(.+)$", head, re.MULTILINE)

            if name_match:
                memories.append({
                    "name": name_match.group(1).strip(),
                    "file": file_path.name,
                    "description": desc_match.group(1).strip() if desc_match else "",
                    "type": type_match.group(1).strip() if type_match else "project",
                    "path": str(file_path.absolute()),
                })

        return memories

    def read_memory(self, name: str) -> Optional[str]:
        """Read the full content of a specific memory file."""
        file_path = self.base_dir / f"{name}.md"
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
