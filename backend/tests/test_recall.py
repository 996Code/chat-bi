"""
T037: Relevant Recall — 按需召回 Agent 记忆

对标:
  - Claude Code §5.4: scanMemoryFiles → formatManifest → 轻量选择
  - config.memory_max_recall_count = 5 (最多召回5条, 不全量灌入)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.ai.recall import recall_memories, format_memories_for_prompt


class TestRecallMemories:
    """按相关性召回最多 max 条记忆。"""

    def test_recall_returns_list(self, tmp_path):
        # 建几个记忆文件
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "gmv.md").write_text(
            "---\nname: gmv\ndescription: GMV 定义和计算口径\ntype: business\n---\nGMV = SUM(total_amount)")
        (mem_dir / "return_rate.md").write_text(
            "---\nname: return_rate\ndescription: 退货率计算\n---\n退货率 = 退货数/总订单")
        (mem_dir / "naming.md").write_text(
            "---\nname: naming\ndescription: 表命名规范\ntype: project\n---\nbiz_ 前缀")

        results = recall_memories("GMV 是多少", memory_dir=str(mem_dir))
        assert isinstance(results, list)
        assert len(results) <= 5  # 最多 max_recall

    def test_recall_relevance_ordering(self, tmp_path):
        """相关记忆排前面 (关键词匹配)。"""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "gmv.md").write_text(
            "---\nname: gmv\ndescription: GMV 计算口径\n---\nGMV content")
        (mem_dir / "naming.md").write_text(
            "---\nname: naming\ndescription: 命名规范\n---\nnaming content")

        results = recall_memories("GMV", memory_dir=str(mem_dir))
        # GMV 相关的应该排第一
        assert results[0]["name"] == "gmv"

    def test_recall_max_count(self, tmp_path):
        """超过 max 条只返回前 max 条。"""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        for i in range(10):
            (mem_dir / f"m{i}.md").write_text(
                f"---\nname: m{i}\ndescription: 记忆{i} 相关\n---\ncontent {i}")
        results = recall_memories("相关", memory_dir=str(mem_dir), max_count=3)
        assert len(results) <= 3

    def test_recall_empty_memory(self, tmp_path):
        """无记忆 → 空列表。"""
        results = recall_memories("任何问题", memory_dir=str(tmp_path / "empty"))
        assert results == []

    def test_recall_no_match_returns_empty(self, tmp_path):
        """问题与所有记忆都不相关 → 空列表 (宁缺毋滥, 不灌无关)。"""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "gmv.md").write_text(
            "---\nname: gmv\ndescription: GMV 计算\n---\ncontent")
        results = recall_memories("完全无关的天气问题xyz", memory_dir=str(mem_dir))
        assert results == []


class TestFormatForPrompt:
    """格式化记忆注入 prompt。"""

    def test_format_empty(self):
        assert format_memories_for_prompt([]) == ""

    def test_format_includes_content(self):
        memories = [
            {"name": "gmv", "description": "GMV口径", "content": "GMV = SUM(total_amount)"},
        ]
        result = format_memories_for_prompt(memories)
        assert "GMV" in result
        assert "SUM(total_amount)" in result
