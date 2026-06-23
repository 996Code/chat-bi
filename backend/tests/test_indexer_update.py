"""
T021: 语义层修改 → 增量更新索引 — 单元测试

对标:
  - RAG-001: 语义层修改后更新索引 (非全库重建)
  - T021 验收: 语义层修改后增量更新 (只更新变更的 Model/Metric)

设计要点:
  - rebuild_index(content, data_source_id, store, embedder):
    删该 data_source 的全部旧索引 → 用新 content 重建 (按源维度, 非全库)
  - 比逐条 diff 简单可靠: 语义层变更不频繁, 全量重建成本可接受
  - 失败降级: 不抛 (对标 build_index)
  - 幂等: 重复 rebuild 结果一致 (删了重建)

为什么删建而非 diff:
  - diff 需要 content 版本对比, 复杂且易错 (增删改列/指标/关系组合)
  - 按 data_source_id 删全量 + 重建, 语义清晰, 对标 RAG-001 "更新索引"
  - 非全库: 只影响当前 data_source, 不动其他源 (delete_by_filter 按 ds 过滤)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.semantic_layer import Column, Model, SemanticModelContent
from app.services.indexer import build_index
from app.services.indexer_update import rebuild_index


def _make_content(table_names: list[str]) -> SemanticModelContent:
    return SemanticModelContent(
        models=[
            Model(
                name=name,
                display_name=name,
                columns=[Column(name="id", display_name="ID", data_type="INTEGER")],
            )
            for name in table_names
        ],
    )


class TestRebuildIndex:
    """rebuild_index: 删旧 + 建新。"""

    @pytest.mark.asyncio
    async def test_rebuild_deletes_old_then_builds_new(self):
        """先删 data_source 的全部旧索引, 再建新。"""
        store = AsyncMock()
        store.delete_by_filter.return_value = 5  # 假设旧的有 5 条
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 3, [0.2] * 3])
        embedder.dim = 3

        result = await rebuild_index(
            content=_make_content(["t1", "t2"]),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )

        # 先删
        store.delete_by_filter.assert_called_once_with({"data_source_id": "ds1"})
        # 再建 (upsert)
        store.upsert.assert_called_once()
        assert result.deleted_count == 5
        assert result.indexed_count == 2  # 2 models

    @pytest.mark.asyncio
    async def test_rebuild_idempotent(self):
        """重复 rebuild 结果一致 (删了重建)。"""
        store = AsyncMock()
        store.delete_by_filter.return_value = 2
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 3, [0.2] * 3])
        embedder.dim = 3

        content = _make_content(["t1", "t2"])
        r1 = await rebuild_index(content, "ds1", store, embedder)
        r2 = await rebuild_index(content, "ds1", store, embedder)

        assert r1.indexed_count == r2.indexed_count == 2

    @pytest.mark.asyncio
    async def test_rebuild_delete_failure_degrades(self):
        """删旧失败 → 不抛, 返回 error (对标降级)。"""
        store = AsyncMock()
        store.delete_by_filter.side_effect = Exception("delete fail")
        embedder = MagicMock()
        embedder.embed = AsyncMock()

        result = await rebuild_index(
            content=_make_content(["t1"]),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        assert result.indexed_count == 0
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_rebuild_embed_failure_after_delete(self):
        """删成功但 embed 失败 → 索引为 0 (旧索引已删, 新的没建成)。"""
        store = AsyncMock()
        store.delete_by_filter.return_value = 3
        embedder = MagicMock()
        embedder.embed = AsyncMock(side_effect=RuntimeError("embed fail"))

        result = await rebuild_index(
            content=_make_content(["t1"]),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        # 删了旧的, 新的没建成
        assert result.deleted_count == 3
        assert result.indexed_count == 0
        store.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_rebuild_empty_content_clears_index(self):
        """空 content → 删光旧索引, 不建新的 (清空)。"""
        store = AsyncMock()
        store.delete_by_filter.return_value = 5
        embedder = MagicMock()
        embedder.embed = AsyncMock()

        result = await rebuild_index(
            content=SemanticModelContent(models=[]),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        assert result.deleted_count == 5
        assert result.indexed_count == 0
        embedder.embed.assert_not_called()
