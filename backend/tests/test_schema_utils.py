"""
Schema 工具测试 — 白名单列 + schema context 从语义层提取

对标: 修复 Phase 4 检查发现的 _extract_allowed_columns 正则 hack bug
(无 display_name 时全漏列, 导致 Layer3 白名单形同虚设)
"""
from __future__ import annotations

import pytest

from app.ai.schema_utils import extract_allowed_columns, build_schema_context, expand_with_relationships
from app.schemas.semantic_layer import Column, Model, Relationship, SemanticModelContent


def _make_content() -> SemanticModelContent:
    return SemanticModelContent(
        models=[
            Model(
                name="biz_orders",
                display_name="订单表",
                columns=[
                    Column(name="id", display_name="id", data_type="BIGINT"),  # 无中文 display
                    Column(name="total_amount", display_name="总金额", data_type="DECIMAL"),
                    Column(name="status", display_name="状态", data_type="VARCHAR"),
                ],
            ),
            Model(
                name="biz_users",
                display_name="用户表",
                columns=[
                    Column(name="id", display_name="id", data_type="BIGINT"),
                    Column(name="name", display_name="姓名", data_type="VARCHAR"),
                ],
            ),
        ],
    )


class TestExtractAllowedColumns:
    """从语义层提取白名单列 (修复正则 hack)。"""

    def test_all_columns_extracted(self):
        """全部表的列都提取 (含无 display_name 的)。"""
        cols = extract_allowed_columns(_make_content())
        assert "id" in cols
        assert "total_amount" in cols
        assert "status" in cols
        assert "name" in cols  # biz_users.name

    def test_filter_by_model_names(self):
        """只取指定表的列。"""
        cols = extract_allowed_columns(_make_content(), ["biz_orders"])
        assert "total_amount" in cols
        assert "name" not in cols  # biz_users 的列不取

    def test_none_content_returns_empty(self):
        assert extract_allowed_columns(None) == set()

    def test_empty_content_returns_empty(self):
        assert extract_allowed_columns(SemanticModelContent(models=[])) == set()

    def test_columns_without_display_name_not_lost(self):
        """关键修复: 无 display_name 的列不能漏 (之前正则 hack 会漏)。"""
        cols = extract_allowed_columns(_make_content())
        # id 没有 display_name (display_name == name), 必须能提取到
        assert "id" in cols


class TestBuildSchemaContext:
    """从语义层构建 schema context (含 data_type)。"""

    def test_context_contains_table_and_columns(self):
        ctx = build_schema_context(_make_content(), ["biz_orders"])
        assert "biz_orders" in ctx
        assert "total_amount" in ctx
        assert "DECIMAL" in ctx  # data_type 对标 RAG-005

    def test_context_filter_by_model(self):
        ctx = build_schema_context(_make_content(), ["biz_users"])
        assert "biz_users" in ctx
        assert "biz_orders" not in ctx

    def test_none_content_returns_empty(self):
        assert build_schema_context(None) == ""

    def test_context_includes_relationships(self):
        """关系提示 (JOIN 依据)。"""
        from app.schemas.semantic_layer import Relationship
        content = SemanticModelContent(
            models=[
                Model(
                    name="biz_orders", display_name="订单",
                    columns=[Column(name="user_id", display_name="用户", data_type="INT")],
                    relationships=[Relationship(
                        name="r", target_model="biz_users", join_type="LEFT",
                        on="biz_orders.user_id = biz_users.id", type="N:1",
                    )],
                ),
            ],
        )
        ctx = build_schema_context(content)
        assert "biz_users" in ctx
        assert "user_id" in ctx


class TestExpandWithRelationships:
    """关系扩展测试 — 含 BFS 防扩散 cap。"""

    def test_basic_expansion(self):
        """1 跳扩展: biz_orders → biz_users。"""
        content = SemanticModelContent(
            models=[
                Model(
                    name="biz_orders", display_name="订单",
                    columns=[Column(name="user_id", display_name="用户", data_type="INT")],
                    relationships=[Relationship(
                        name="r", target_model="biz_users", join_type="LEFT",
                        on="biz_orders.user_id = biz_users.id", type="N:1",
                    )],
                ),
                Model(
                    name="biz_users", display_name="用户",
                    columns=[Column(name="id", display_name="id", data_type="BIGINT")],
                ),
            ],
        )
        result = expand_with_relationships(content, ["biz_orders"])
        assert "biz_orders" in result
        assert "biz_users" in result

    def test_bfs_cap_prevents_explosion(self):
        """超级枢纽不应对把全库拉进来 (BFS cap 限流)。"""
        # 构造: hub 表连接到 30 张叶子表 (超级枢纽)
        models = [
            Model(
                name="hub", display_name="枢纽",
                columns=[Column(name="id", display_name="id", data_type="BIGINT")],
                relationships=[
                    Relationship(
                        name=f"r{i}", target_model=f"leaf_{i}", join_type="LEFT",
                        on=f"hub.id = leaf_{i}.hub_id", type="1:N",
                    )
                    for i in range(30)
                ],
            ),
        ]
        # 加 30 张叶子表
        for i in range(30):
            models.append(Model(
                name=f"leaf_{i}", display_name=f"叶子{i}",
                columns=[Column(name="hub_id", display_name="枢纽ID", data_type="BIGINT")],
            ))

        content = SemanticModelContent(models=models)
        # 种子表 = hub, max_total = 10 → 应该只返回 10 张 (hub + 9 叶子)
        result = expand_with_relationships(content, ["hub"], max_total=10)
        assert len(result) == 10
        assert "hub" in result

    def test_seed_tables_always_preserved(self):
        """种子表永远保留, 即使超过 max_total。"""
        # 5 张种子表 + 每张有 5 个邻居
        models = []
        for i in range(5):
            models.append(Model(
                name=f"seed_{i}", display_name=f"种子{i}",
                columns=[Column(name="id", display_name="id", data_type="BIGINT")],
                relationships=[
                    Relationship(
                        name=f"r{j}", target_model=f"extra_{i}_{j}", join_type="LEFT",
                        on=f"seed_{i}.id = extra_{i}_{j}.sid", type="1:N",
                    )
                    for j in range(5)
                ],
            ))
        for i in range(5):
            for j in range(5):
                models.append(Model(
                    name=f"extra_{i}_{j}", display_name=f"扩展{i}{j}",
                    columns=[Column(name="sid", display_name="sid", data_type="BIGINT")],
                ))

        content = SemanticModelContent(models=models)
        seeds = [f"seed_{i}" for i in range(5)]
        result = expand_with_relationships(content, seeds, max_total=3)
        # 种子 5 张必须全在 (不受 max_total 影响), 但不会加更多
        for s in seeds:
            assert s in result
        assert len(result) <= 3 + 5  # 不超过 seed + cap
