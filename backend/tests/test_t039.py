"""
T039: 对话摘要保留 — 压缩摘要存储 + 关键决策标注

对标:
  - CMP-003: 压缩后摘要可展开查看 + 关键决策点标注
  - 用户确认过的表选择/SQL修改 不丢失
"""
from __future__ import annotations

import pytest

from app.ai.state_store import ConversationState, StateStore, DecisionPoint


class TestDecisionPoint:
    """关键决策点 (用户确认的表选择/SQL修改)。"""

    def test_create_decision(self):
        d = DecisionPoint(
            type="table_confirm",
            description="用户确认查询 biz_orders 表",
            detail={"table": "biz_orders"},
        )
        assert d.type == "table_confirm"
        assert d.detail["table"] == "biz_orders"

    def test_decision_to_dict(self):
        d = DecisionPoint(type="sql_modify", description="用户修改了SQL", detail={})
        assert d.to_dict()["type"] == "sql_modify"


class TestStateStoreWithSummary:
    """StateStore 存储压缩摘要 + 决策点。"""

    def test_save_with_summary(self, tmp_path):
        store = StateStore(base_dir=str(tmp_path / "state"))
        state = ConversationState(current_sql="SELECT 1")
        state.compressed_summary = "用户查了本月销售额"
        store.save("t", "c", 1, state)

        loaded = store.load("t", "c")
        assert loaded.compressed_summary == "用户查了本月销售额"

    def test_save_with_decisions(self, tmp_path):
        store = StateStore(base_dir=str(tmp_path / "state"))
        state = ConversationState()
        state.decisions = [
            DecisionPoint(type="table_confirm", description="确认查 biz_orders", detail={}),
        ]
        store.save("t", "c", 1, state)

        loaded = store.load("t", "c")
        assert len(loaded.decisions) == 1
        assert loaded.decisions[0].type == "table_confirm"

    def test_list_summaries(self, tmp_path):
        """列出对话的所有轮次摘要 (供前端展开查看)。"""
        store = StateStore(base_dir=str(tmp_path / "state"))
        s1 = ConversationState(current_sql="SELECT 1")
        s1.compressed_summary = "第一轮摘要"
        s2 = ConversationState(current_sql="SELECT 2")
        s2.compressed_summary = "第二轮摘要"
        store.save("t", "c", 1, s1)
        store.save("t", "c", 2, s2)

        summaries = store.list_turns("t", "c")
        assert len(summaries) == 2
        assert summaries[0]["turn"] == 1
