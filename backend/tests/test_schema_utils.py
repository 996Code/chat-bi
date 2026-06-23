"""
Schema 工具测试 — 白名单列 + schema context 从语义层提取

对标: 修复 Phase 4 检查发现的 _extract_allowed_columns 正则 hack bug
(无 display_name 时全漏列, 导致 Layer3 白名单形同虚设)
"""
from __future__ import annotations

import pytest

from app.ai.schema_utils import extract_allowed_columns, build_schema_context
from app.schemas.semantic_layer import Column, Model, SemanticModelContent


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
