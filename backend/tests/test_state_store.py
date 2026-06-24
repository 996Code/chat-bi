"""
T036: State Store — 单元测试

对标:
  - AEE-005: 结构化状态 (current_tables/sql/filters/result_summary/chart_type)
  - 追问维度继承: 追问"上个月呢"→ 从 state 读锚点 + 相对时间展开
"""
from __future__ import annotations

import pytest

from app.ai.state_store import ConversationState, StateStore


class TestConversationState:
    """结构化状态 (对标 AEE-005)。"""

    def test_create_state(self):
        s = ConversationState(
            current_tables=["biz_orders", "biz_users"],
            current_sql="SELECT SUM(total_amount) FROM biz_orders WHERE month='2026-06'",
            current_filters={"month": "2026-06"},
            result_summary={"row_count": 1, "key_numbers": {"total": 125000}},
            chart_type="bar",
        )
        assert s.current_tables == ["biz_orders", "biz_users"]
        assert "biz_orders" in s.current_sql

    def test_state_to_dict_roundtrip(self):
        s = ConversationState(
            current_tables=["t1"],
            current_sql="SELECT 1",
            current_filters={},
            result_summary={},
            chart_type=None,
        )
        d = s.to_dict()
        s2 = ConversationState.from_dict(d)
        assert s2.current_sql == "SELECT 1"
        assert s2.current_tables == ["t1"]

    def test_empty_state(self):
        s = ConversationState()
        assert s.current_tables == []
        assert s.current_sql == ""
        d = s.to_dict()
        assert d["current_sql"] == ""


class TestStateStore:
    """State Store: save/load/restore (对标 AEE-005 Checkpointer)。"""

    def test_save_and_load(self):
        store = StateStore(base_dir="/tmp/test_state_store")
        state = ConversationState(
            current_tables=["biz_orders"],
            current_sql="SELECT COUNT(*) FROM biz_orders",
            current_filters={"status": "paid"},
            result_summary={"row_count": 1, "key_numbers": {"count": 79}},
            chart_type="bar",
        )
        store.save("tenant1", "conv1", 1, state)

        loaded = store.load("tenant1", "conv1")
        assert loaded is not None
        assert loaded.current_sql == "SELECT COUNT(*) FROM biz_orders"
        assert loaded.current_filters == {"status": "paid"}

    def test_load_nonexistent_returns_none(self):
        store = StateStore(base_dir="/tmp/test_state_store")
        assert store.load("tenant1", "nonexistent") is None

    def test_multi_turn_latest_wins(self):
        """多轮对话, load 返回最后一轮状态。"""
        store = StateStore(base_dir="/tmp/test_state_store2")
        s1 = ConversationState(current_sql="SELECT 1", current_tables=["t1"])
        s2 = ConversationState(current_sql="SELECT 2", current_tables=["t2"])
        store.save("t", "c", 1, s1)
        store.save("t", "c", 2, s2)
        loaded = store.load("t", "c")
        assert loaded.current_sql == "SELECT 2"

    def test_tenant_isolation(self):
        """不同租户的状态隔离。"""
        store = StateStore(base_dir="/tmp/test_state_store3")
        store.save("t1", "c", 1, ConversationState(current_sql="SELECT 1"))
        store.save("t2", "c", 1, ConversationState(current_sql="SELECT 2"))
        assert store.load("t1", "c").current_sql == "SELECT 1"
        assert store.load("t2", "c").current_sql == "SELECT 2"


class TestFollowUpInheritance:
    """追问维度继承 (对标海泰 intent_history)。"""

    def test_inherit_unchanged_dimensions(self):
        """追问"上个月呢"→ 未提及的维度继承锚点 (表/聚合方式)。"""
        prev = ConversationState(
            current_tables=["biz_orders"],
            current_sql="SELECT category, SUM(total_amount) FROM biz_orders WHERE month='2026-06' GROUP BY category",
            current_filters={"month": "2026-06"},
        )
        # 追问"上个月呢" → month 维度变化, 其他继承
        new_filters = prev.inherit_filters({"month": "2026-05"})
        assert new_filters["month"] == "2026-05"  # 本轮新值覆盖
        # 其他维度 (如有) 继承

    def test_explicit_override(self):
        """本轮显式声明的维度覆盖锚点。"""
        prev = ConversationState(
            current_filters={"month": "2026-06", "category": "数码"},
        )
        new = prev.inherit_filters({"category": "服装"})
        assert new["category"] == "服装"  # 覆盖
        assert new["month"] == "2026-06"  # 未提及继承
