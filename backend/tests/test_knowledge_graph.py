"""
T016: 知识图谱 AI 推断 — 单元测试

对标:
  - SEM-003 (openspec spec): 知识图谱推断 + confidence/source 标注
  - T016 验收: 推断结果标注 source + confidence；不自动写回（人工审核）

设计要点:
  - name_pattern: xxx_id → xxx 命名模式 (confidence=0.6)
  - ai_inferred: LLM 推断表关系 (confidence=0.7)
  - 已有 FK 关系不重复推断 (去重)
  - 只返回建议，不修改输入 content
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.semantic_layer import (
    Column,
    Model,
    Relationship,
    SemanticModelContent,
)
from app.services.knowledge_graph import (
    _infer_relationships_by_name,
    infer_knowledge_graph,
)


# ── 测试夹具 ──────────────────────────────────────────────────

def _make_column(name: str, data_type: str = "VARCHAR") -> Column:
    return Column(
        name=name,
        display_name=name,
        data_type=data_type,
        source="manual",
        confidence=1.0,
    )


def _make_model(name: str, col_names: list[str]) -> Model:
    return Model(
        name=name,
        display_name=name,
        columns=[_make_column(c) for c in col_names],
        source="manual",
        confidence=1.0,
    )


def _orders_content() -> SemanticModelContent:
    """biz_orders(user_id, product_id) + biz_users(id) + biz_products(id)."""
    return SemanticModelContent(
        models=[
            _make_model("biz_orders", ["id", "user_id", "product_id", "total_amount"]),
            _make_model("biz_users", ["id", "name", "email"]),
            _make_model("biz_products", ["id", "name", "price"]),
        ],
    )


# ── 1. _infer_relationships_by_name: xxx_id → xxx 模式 ─────────

class TestInferByName:
    """命名模式推断：xxx_id → xxx，confidence=0.6，source=name_pattern。"""

    def test_user_id_points_to_users_table(self):
        content = _orders_content()
        orders = content.models[0]
        rels = _infer_relationships_by_name(orders, [m.name for m in content.models])

        targets = [r.target_model for r in rels]
        assert "biz_users" in targets, f"user_id 应推断到 biz_users, got {targets}"
        assert "biz_products" in targets, f"product_id 应推断到 biz_products, got {targets}"

    def test_name_pattern_confidence_and_source(self):
        content = _orders_content()
        orders = content.models[0]
        rels = _infer_relationships_by_name(orders, [m.name for m in content.models])

        for r in rels:
            assert r.source == "name_pattern"
            assert r.confidence == pytest.approx(0.6)

    def test_no_id_column_yields_no_relationships(self):
        """没有 xxx_id 列的表不应产生 name_pattern 关系。"""
        users = _make_model("biz_users", ["id", "name", "email"])
        rels = _infer_relationships_by_name(users, ["biz_users", "biz_orders"])
        assert rels == []

    def test_on_clause_format(self):
        """ON 条件格式: <table>.<col> = <target>.id"""
        content = _orders_content()
        orders = content.models[0]
        rels = _infer_relationships_by_name(orders, [m.name for m in content.models])

        user_rel = next(r for r in rels if r.target_model == "biz_users")
        assert user_rel.on == "biz_orders.user_id = biz_users.id"

    def test_skips_when_target_table_missing(self):
        """xxx_id 但目标表不存在 → 不推断（避免幻觉）。"""
        orders = _make_model("biz_orders", ["id", "ghost_id", "total_amount"])
        rels = _infer_relationships_by_name(orders, ["biz_orders", "biz_users"])
        targets = [r.target_model for r in rels]
        assert "biz_ghost" not in targets
        assert targets == []

    def test_table_name_prefix_biz_is_stripped_for_match(self):
        """biz_orders.user_id → biz_users (biz_ 前缀要处理)。"""
        orders = _make_model("biz_orders", ["id", "user_id"])
        rels = _infer_relationships_by_name(orders, ["biz_orders", "biz_users"])
        assert len(rels) == 1
        assert rels[0].target_model == "biz_users"


# ── 2. infer_knowledge_graph: 聚合 + 去重 + 不写回 ──────────────

class TestInferKnowledgeGraph:
    """聚合 name_pattern + ai_inferred，去重已有 FK，不修改输入。"""

    @pytest.mark.asyncio
    async def test_aggregates_name_pattern_sources(self):
        content = _orders_content()
        suggestions = await infer_knowledge_graph(content, use_llm=False)

        # biz_orders 应该有到 biz_users 和 biz_products 的 name_pattern 建议
        targets = {r.target_model for r in suggestions}
        assert "biz_users" in targets
        assert "biz_products" in targets

    @pytest.mark.asyncio
    async def test_does_not_duplicate_existing_fk(self):
        """已有 FK 关系 → 不再产生 name_pattern 建议（去重）。"""
        content = _orders_content()
        # 给 biz_orders 加一个已有的 FK 关系到 biz_users
        content.models[0].relationships.append(Relationship(
            name="biz_orders_to_biz_users",
            target_model="biz_users",
            join_type="LEFT",
            on="biz_orders.user_id = biz_users.id",
            type="N:1",
            source="foreign_key",
            confidence=1.0,
        ))

        suggestions = await infer_knowledge_graph(content, use_llm=False)
        user_suggestions = [r for r in suggestions if r.target_model == "biz_users"]
        assert user_suggestions == [], "已有 FK 到 biz_users，不应再重复推断"

    @pytest.mark.asyncio
    async def test_does_not_mutate_input_content(self):
        """只返回建议，不写回 content（人工审核后才进语义层）。"""
        content = _orders_content()
        original_rel_count = sum(len(m.relationships) for m in content.models)

        await infer_knowledge_graph(content, use_llm=False)

        after_rel_count = sum(len(m.relationships) for m in content.models)
        assert after_rel_count == original_rel_count, "infer_knowledge_graph 不应修改输入 content"

    @pytest.mark.asyncio
    async def test_llm_suggestions_have_ai_inferred_source(self):
        """注入 mock LLM client，验证 ai_inferred 来源标注。"""
        content = _orders_content()

        # mock LLM 返回一条 ai_inferred 建议 (批量格式: 含 source_model)
        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = json.dumps([
            {
                "source_model": "biz_orders",
                "target_model": "biz_users",
                "on": "biz_orders.user_id = biz_users.id",
                "type": "N:1",
            }
        ])

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

        suggestions = await infer_knowledge_graph(content, use_llm=True, llm_client=fake_client)

        ai_rels = [r for r in suggestions if r.source == "ai_inferred"]
        assert len(ai_rels) >= 1, "应有 ai_inferred 来源的建议"
        for r in ai_rels:
            assert r.confidence == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_llm_failure_degrades_gracefully(self):
        """LLM 调用失败 → 降级为只有 name_pattern（不抛异常）。"""
        content = _orders_content()

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(side_effect=Exception("LLM down"))

        suggestions = await infer_knowledge_graph(content, use_llm=True, llm_client=fake_client)

        # 不抛异常，且仍有 name_pattern 建议
        name_pattern_rels = [r for r in suggestions if r.source == "name_pattern"]
        assert len(name_pattern_rels) >= 1

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty(self):
        content = SemanticModelContent(models=[])
        suggestions = await infer_knowledge_graph(content, use_llm=False)
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_llm_invalid_json_degrades_gracefully(self):
        """LLM 返回非法 JSON → 降级（对标 infer_column_chinese 行为）。"""
        content = _orders_content()

        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = "这不是JSON{{{"

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

        suggestions = await infer_knowledge_graph(content, use_llm=True, llm_client=fake_client)

        # 降级：只剩 name_pattern，不抛
        sources = {r.source for r in suggestions}
        assert "name_pattern" in sources
        assert "ai_inferred" not in sources
