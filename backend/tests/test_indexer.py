"""
T020: 扫描后自动建索引 — 单元测试

对标:
  - RAG-001 (openspec spec): 数据源接入时自动构建 Embedding 索引
  - T020 验收: 新数据源接入后 5 分钟内完成索引构建

设计要点:
  - build_index(content, data_source_id): 把 SemanticModelContent 转成
    VectorRecord 列表 → embed → upsert 到 vector_store
  - 索引对象: Model (表名+描述+列名), Metric, CalculatedField
  - 失败降级: embed/vector_store 失败不阻塞扫描 (对标 LLM enrich)
  - 幂等: 同 content 重新 build, 同 id 覆盖 (vector_store.upsert 语义)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.semantic_layer import (
    Column,
    Metric,
    Model,
    SemanticModelContent,
)
from app.services.indexer import build_index, model_to_text, metric_to_text


# ── 文本序列化 ────────────────────────────────────────────────

class TestModelToText:
    """Model → 可 embed 的文本 (表名+描述+列名+列描述)。"""

    def test_basic_model_text(self):
        model = Model(
            name="biz_orders",
            display_name="订单表",
            description="存储订单信息",
            columns=[
                Column(name="id", display_name="ID", data_type="INTEGER"),
                Column(name="total_amount", display_name="总金额", data_type="DECIMAL"),
            ],
        )
        text = model_to_text(model)
        assert "biz_orders" in text
        assert "订单表" in text
        assert "total_amount" in text
        assert "总金额" in text

    def test_text_includes_column_datatype(self):
        """data_type 也要 embed (对标 RAG-005 类型约束)。"""
        model = Model(
            name="t1",
            display_name="T1",
            columns=[Column(name="price", display_name="价格", data_type="DECIMAL")],
        )
        text = model_to_text(model)
        assert "DECIMAL" in text


class TestMetricToText:
    """Metric → 文本 (名称+公式+描述)。"""

    def test_basic_metric_text(self):
        metric = Metric(
            name="gmv",
            display_name="GMV",
            formula="SUM(total_amount)",
            description="总成交额",
        )
        text = metric_to_text(metric)
        assert "gmv" in text
        assert "GMV" in text
        assert "SUM(total_amount)" in text
        assert "总成交额" in text


# ── build_index ───────────────────────────────────────────────

def _make_content() -> SemanticModelContent:
    return SemanticModelContent(
        models=[
            Model(
                name="biz_orders",
                display_name="订单表",
                description="订单数据",
                columns=[
                    Column(name="total_amount", display_name="总金额", data_type="DECIMAL"),
                    Column(name="user_id", display_name="用户ID", data_type="INTEGER"),
                ],
                metrics=[
                    Metric(name="gmv", display_name="GMV", formula="SUM(total_amount)"),
                ],
            ),
            Model(
                name="biz_users",
                display_name="用户表",
                columns=[Column(name="name", display_name="姓名", data_type="VARCHAR")],
            ),
        ],
    )


class TestBuildIndex:
    """build_index: content → embed → upsert。"""

    @pytest.mark.asyncio
    async def test_build_index_upserts_all_models(self):
        """每个 Model 生成一条 VectorRecord。"""
        store = AsyncMock()
        embedder = MagicMock()
        # 模拟 embed: 每个文本返回一个 3 维向量
        embedder.embed = AsyncMock(return_value=[[0.1] * 3, [0.2] * 3, [0.3] * 3])
        embedder.dim = 3

        content = _make_content()
        await build_index(
            content=content,
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )

        store.upsert.assert_called_once()
        records = store.upsert.call_args.kwargs.get("records") or store.upsert.call_args[0][0]
        # 2 models + 1 metric = 3 records
        assert len(records) == 3
        ids = [r.id for r in records]
        assert "ds1:model:biz_orders" in ids
        assert "ds1:model:biz_users" in ids
        assert "ds1:metric:gmv" in ids

    @pytest.mark.asyncio
    async def test_build_index_metadata_has_data_source(self):
        """VectorRecord.metadata 带 data_source_id (对标 RAG-005 标量过滤)。"""
        store = AsyncMock()
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 3])
        embedder.dim = 3

        await build_index(
            content=_make_content(),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        records = store.upsert.call_args.kwargs.get("records") or store.upsert.call_args[0][0]
        for r in records:
            assert r.metadata["data_source_id"] == "ds1"

    @pytest.mark.asyncio
    async def test_build_index_embed_failure_degrades(self):
        """embed 失败 → 不抛 (降级, 对标 LLM enrich)。"""
        store = AsyncMock()
        embedder = MagicMock()
        embedder.embed = AsyncMock(side_effect=RuntimeError("embed fail"))

        # 不应抛异常
        result = await build_index(
            content=_make_content(),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        assert result.indexed_count == 0  # 失败 → 0 条索引
        store.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_index_empty_content(self):
        """空 content → 0 条索引, 不调 embed。"""
        store = AsyncMock()
        embedder = MagicMock()
        embedder.embed = AsyncMock()

        result = await build_index(
            content=SemanticModelContent(models=[]),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        assert result.indexed_count == 0
        embedder.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_index_returns_count(self):
        store = AsyncMock()
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 3] * 3)
        embedder.dim = 3

        result = await build_index(
            content=_make_content(),
            data_source_id="ds1",
            store=store,
            embedder=embedder,
        )
        assert result.indexed_count == 3  # 2 models + 1 metric
