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
        # 启动时自动清理幽灵索引 (仅首次 list_memories 触发一次)
        self._index_reconciled = False

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
        extra_metadata: dict | None = None,
    ) -> Path:
        """Save a memory file and update the index.

        Args:
            name: Human-readable title (editable)
            description: One-line summary for the index and recall
            content: The memory content (markdown body)
            memory_type: user | feedback | project | reference | linkage
            mem_id: Existing UUID to update (None = create new)
            extra_metadata: 额外 metadata 字段 (E1: linkage 用 co_occurrence/tables;
                            conversation_id 标记来源对话)

        Returns:
            Path to the created memory file.
        """
        from datetime import datetime, timezone

        self._init_index()

        # created_at: 新建时写入当前时间, 更新时保留原值
        created_at: str | None = None
        if mem_id:
            # 更新已有记忆: filename = {mem_id}.md
            file_name = f"{mem_id}.md"
            # 读取已有 frontmatter 中的 created_at
            existing_path = self.base_dir / file_name
            if existing_path.exists():
                existing_text = existing_path.read_text(encoding="utf-8")
                m = re.search(r"^created_at:\s*(.+)$", existing_text, re.MULTILINE)
                if m:
                    created_at = m.group(1).strip()
        else:
            # 新建: 生成 UUID + 当前时间
            mem_id = str(uuid_mod.uuid4())
            file_name = f"{mem_id}.md"
            created_at = datetime.now(timezone.utc).isoformat()

        file_path = self.base_dir / file_name

        # metadata 段: 基础 (type/consolidated) + 额外 (co_occurrence/tables 等)
        meta_lines = ["metadata:", f"  type: {memory_type}", "  consolidated: false"]
        if extra_metadata:
            import yaml as _yaml  # lazy: 仅写记忆时才 import
            for k, v in extra_metadata.items():
                # key 必须是合法 YAML 标识符 (防御: 避免特殊字符破坏 frontmatter)
                if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", str(k)):
                    raise ValueError(f"Invalid extra_metadata key: {k!r}")
                # 用 yaml.dump 序列化值 (支持标量/列表/嵌套 dict/list)
                # default_flow_style=True 对简单值用行内格式 (co_occurrence: 3),
                # 对嵌套结构自动用块格式 (可读性好)
                dumped = _yaml.dump({k: v}, default_flow_style=False).strip()
                # yaml.dump 输出 "key: value\n", 取掉首行 key 前缀, 保留缩进
                # 简单值: "co_occurrence: 3" → "  co_occurrence: 3"
                # 嵌套值: "join_paths:\n- on: ...\n  join_type: ..." → "  join_paths:\n    - on: ...\n      join_type: ..."
                lines = dumped.split("\n")
                # 首行是 "key: value" 或 "key:\n  - ...", 统一加 2 空格缩进
                indented = "\n".join("  " + line if i == 0 else "  " + line for i, line in enumerate(lines))
                meta_lines.append(indented)
        frontmatter = (
            f"---\n"
            f"id: {mem_id}\n"
            f"name: {name}\n"
            f"description: {description}\n"
            + (f"created_at: {created_at}\n" if created_at else "")
            + "\n".join(meta_lines) + "\n"
            f"---\n\n"
            f"{content}\n"
        )
        file_path.write_text(frontmatter, encoding="utf-8")

        # Update index (pass name for linkage dedup)
        self._update_index_link(file_name, description, memory_name=name)

        logger.info("Memory saved: %s (%s)", name, memory_type)
        return file_path

    def _update_index_link(self, file_name: str, description: str, *, memory_name: str | None = None) -> None:
        """Add or update a link entry in MEMORY.md.

        Args:
            file_name: The .md filename (UUID stem)
            description: One-line description for the index
            memory_name: The memory's name field (e.g. "linkage-biz_orders-uc_users").
                         Used for dedup: if a linkage memory with the same name but
                         different UUID already exists, the old entry is removed first.
        """
        index_content = self.index_path.read_text(encoding="utf-8")
        link_line = f"- [{file_name.replace('.md', '')}]({file_name}) — {description}"

        # Replace existing entry for the same UUID
        existing_pattern = re.compile(
            rf"^- \[{re.escape(file_name.replace('.md', ''))}\]\(.*\) — .*$",
            re.MULTILINE,
        )
        if existing_pattern.search(index_content):
            index_content = existing_pattern.sub(link_line, index_content)
        else:
            index_content += f"{link_line}\n"

        # Dedup by memory name for linkage type: remove stale entries with same name but different UUID
        if memory_name and memory_name.startswith("linkage-"):
            # Find all index entries whose linked file has this memory name in frontmatter
            # Simple approach: scan for entries where the description matches the linkage pattern
            # and the UUID is different from current file_name
            current_stem = file_name.replace(".md", "")
            # Remove any other linkage entry with the same description (same table pair)
            # Pattern: - [different-uuid](different-uuid.md) — 表 X 和 Y 的共现经验
            desc_escaped = re.escape(description)
            stale_pattern = re.compile(
                rf"^- \[(?!{re.escape(current_stem)})[a-f0-9\-]+\]\([a-f0-9\-]+\.md\) — {desc_escaped}$\n?",
                re.MULTILINE,
            )
            index_content = stale_pattern.sub("", index_content)

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

    def reconcile_index(self) -> int:
        """清理幽灵索引条目 (索引指向的文件不存在) + 去重同名 linkage 条目。

        Returns:
            清理的条目数
        """
        self._init_index()
        index_content = self.index_path.read_text(encoding="utf-8")
        lines = index_content.split("\n")
        clean_lines: list[str] = []
        removed = 0

        # 收集所有 linkage 描述 → UUID 的映射 (用于去重)
        linkage_desc_to_stem: dict[str, str] = {}

        for line in lines:
            # 匹配索引行: - [uuid](uuid.md) — description
            m = re.match(r"^- \[([a-f0-9\-]+)\]\(\1\.md\) — (.+)$", line)
            if not m:
                clean_lines.append(line)
                continue
            stem = m.group(1)
            desc = m.group(2)

            # 检查文件是否存在
            if not (self.base_dir / f"{stem}.md").exists():
                removed += 1
                logger.info("幽灵索引清理: %s (文件不存在)", stem)
                continue

            # linkage 去重: 同一 description 只保留最新 (文件修改时间最晚的)
            if desc.startswith("表 ") and "的共现经验" in desc:
                if desc in linkage_desc_to_stem:
                    existing_stem = linkage_desc_to_stem[desc]
                    existing_mtime = (self.base_dir / f"{existing_stem}.md").stat().st_mtime
                    current_mtime = (self.base_dir / f"{stem}.md").stat().st_mtime
                    if current_mtime >= existing_mtime:
                        # 当前更新, 删旧的
                        removed += 1
                        logger.info("linkage 索引进重: 删除旧条目 %s (保留 %s)", existing_stem, stem)
                        # 从 clean_lines 中移除旧条目
                        clean_lines = [cl for cl in clean_lines
                                       if not cl.startswith(f"- [{existing_stem}]")]
                        linkage_desc_to_stem[desc] = stem
                    else:
                        # 旧更新, 跳过当前
                        removed += 1
                        logger.info("linkage 索引进重: 跳过当前 %s (保留 %s)", stem, existing_stem)
                        continue
                else:
                    linkage_desc_to_stem[desc] = stem

            clean_lines.append(line)

        if removed > 0:
            new_content = "\n".join(clean_lines)
            # 清理尾部多余空行
            while new_content.endswith("\n\n\n"):
                new_content = new_content[:-1]
            self.index_path.write_text(new_content, encoding="utf-8")
            logger.info("索引清理完成: 移除 %d 条幽灵/重复条目", removed)

        return removed

    def list_memories(self) -> list[dict[str, str]]:
        """List all memory files with their metadata (for API + recall).

        对标 Claude Code: scanMemoryFiles() — scan file headers only,
        return id/name/description/type for the lightweight model to select.
        """
        # 首次调用时自动清理幽灵索引
        if not self._index_reconciled:
            self._index_reconciled = True
            try:
                removed = self.reconcile_index()
                if removed:
                    logger.info("启动索引清理: 移除 %d 条幽灵/重复条目", removed)
            except Exception as e:
                logger.warning("启动索引清理失败 (不阻塞): %s", e)

        import yaml as _yaml  # lazy: 仅扫描时才 import
        memories = []
        for file_path in self.base_dir.glob("*.md"):
            if file_path.name == "MEMORY.md":
                continue

            # Read only the frontmatter (从开头到第二个 ---) for fast scanning
            full_text = file_path.read_text(encoding="utf-8")
            parts = full_text.split("---", 2)
            has_frontmatter = len(parts) >= 3 and full_text.startswith("---")

            if has_frontmatter:
                head = parts[1]
                try:
                    fm = _yaml.safe_load(head) or {}
                except Exception:
                    fm = {}
            else:
                fm = {}

            # id: 优先 frontmatter, 回退 filename (兼容旧格式无 id 的种子文件)
            mem_id = str(fm.get("id", "")).strip() or file_path.stem
            metadata = fm.get("metadata", {}) or {}

            entry: dict = {
                "id": mem_id,
                "name": fm.get("name", file_path.stem),
                "file": file_path.name,
                "description": fm.get("description", ""),
                "type": metadata.get("type", "project"),
                "consolidated": metadata.get("consolidated", False),
                "path": str(file_path.absolute()),
            }
            # created_at: 顶层 frontmatter 字段 (非 metadata 内)
            if fm.get("created_at"):
                entry["created_at"] = str(fm["created_at"])
            # conversation_id: 来源对话 (metadata 内, 对话创建的记忆才有)
            if "conversation_id" in metadata:
                entry["conversation_id"] = str(metadata["conversation_id"])
            # E1: linkage 记忆的额外字段 (其他类型无此字段, 不污染 entry)
            if "co_occurrence" in metadata:
                entry["co_occurrence"] = int(metadata["co_occurrence"])
            if "tables" in metadata:
                entry["tables"] = metadata["tables"]
            if "join_paths" in metadata:
                entry["join_paths"] = metadata["join_paths"]
            if "scenes" in metadata:
                entry["scenes"] = metadata["scenes"]
            if "aggregation" in metadata:
                entry["aggregation"] = metadata["aggregation"]
            memories.append(entry)

        return memories

    def get_linkage_memory(self, table_a: str, table_b: str) -> dict | None:
        """按表对查询 linkage 记忆 (文件名是 UUID, 不能按名查; 遍历 metadata.tables 匹配)。

        E1 Task 1.1: 表对按字典序规范化后匹配, 支持 caller 乱序传入。

        Returns:
            匹配的记忆 entry (含 id/co_occurrence/tables 等), 无匹配返回 None。
        """
        pair = sorted([table_a, table_b])
        for m in self.list_memories():
            if m.get("type") != "linkage":
                continue
            tables = m.get("tables")
            if tables and sorted(tables) == pair:
                return m
        return None

    def mark_consolidated(self, mem_id: str) -> bool:
        """标记记忆为已整理 (在 frontmatter 设 consolidated: true)。

        整理后原始记忆不删除, 但默认隐藏, 用户可通过开关查看。
        """
        stem = mem_id.replace(".md", "")
        file_path = self.base_dir / f"{stem}.md"
        if not file_path.exists():
            return False
        try:
            content = file_path.read_text(encoding="utf-8")
            # 如果已有 consolidated: true, 跳过
            if re.search(r"^\s*consolidated:\s*true", content, re.MULTILINE):
                return True
            # 替换已有的 consolidated: false → true (而非追加, 避免重复键)
            new_content = re.sub(
                r"^(\s*)consolidated:\s*false",
                r"\1consolidated: true",
                content,
                flags=re.MULTILINE,
            )
            if new_content != content:
                file_path.write_text(new_content, encoding="utf-8")
                return True
            # 无已有 consolidated 行 → 在 metadata 段的 type 行后插入
            if "metadata:" in content:
                original = content
                content = re.sub(
                    r"(metadata:\n(\s+)type: .+\n)",
                    r"\1\2consolidated: true\n",
                    content,
                )
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
