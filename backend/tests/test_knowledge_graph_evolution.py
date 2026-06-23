"""
T017: 知识图谱演化 — 单元测试

对标:
  - SEM-003 演化: 历史查询挖掘 + 反馈回流
  - 注: 完整 e2e（真实 SavedQuery/Feedback 数据）留到 Phase 6，
        本 Phase 只验证算法正确性（mock 数据）

设计要点:
  - mine_implicit_relationships: 频繁 JOIN 表对 → confidence 提升
  - apply_feedback_signals: 纠正 → 下降；点赞 → 提升（纠正优先级更高）
  - 三态审核 (v1 教训 #41): corrections/praises 分离
"""
from __future__ import annotations

import pytest

from app.schemas.semantic_layer import Relationship
from app.services.knowledge_graph import (
    FREQUENT_JOIN_BOOST,
    FREQUENT_JOIN_THRESHOLD,
    MAX_CONFIDENCE,
    MIN_CONFIDENCE,
    CORRECTION_PENALTY,
    PRAISE_BOOST,
    apply_feedback_signals,
    mine_implicit_relationships,
)


def _make_rel(from_t: str, to_t: str, confidence: float = 0.7) -> Relationship:
    return Relationship(
        name=f"{from_t}_to_{to_t}",
        target_model=to_t,
        join_type="LEFT",
        on=f"{from_t}.x = {to_t}.id",
        type="N:1",
        source="ai_inferred",
        confidence=confidence,
    )


# ── mine_implicit_relationships ───────────────────────────────

class TestMineImplicit:
    """历史查询挖掘：频繁 JOIN → confidence 提升。"""

    def test_frequent_join_boosts_confidence(self):
        """同表对在 >= 阈值条查询里共现 → confidence 提升。"""
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.5)]
        # 构造 FREQUENT_JOIN_THRESHOLD 条查询都 JOIN 这两个表
        # 0.5 + 0.1*3 = 0.8 < MAX，不会触发封顶
        sql = "SELECT * FROM biz_orders JOIN biz_users ON biz_orders.user_id = biz_users.id"
        history = [sql] * FREQUENT_JOIN_THRESHOLD

        suggestions = mine_implicit_relationships(history, rels)

        assert ("biz_orders", "biz_users") in suggestions
        boosted = suggestions[("biz_orders", "biz_users")]
        assert boosted > 0.5
        assert boosted == pytest.approx(0.5 + FREQUENT_JOIN_BOOST * FREQUENT_JOIN_THRESHOLD)

    def test_below_threshold_no_boost(self):
        """共现次数 < 阈值 → 不提升。"""
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.7)]
        sql = "SELECT * FROM biz_orders JOIN biz_users ON ..."
        history = [sql] * (FREQUENT_JOIN_THRESHOLD - 1)

        suggestions = mine_implicit_relationships(history, rels)
        assert suggestions == {}

    def test_unrelated_tables_ignored(self):
        """查询里的表对不在已知关系里 → 忽略。"""
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.7)]
        # 查询里只有 biz_products，不涉及已知关系
        history = ["SELECT * FROM biz_products"] * 10

        suggestions = mine_implicit_relationships(history, rels)
        assert suggestions == {}

    def test_boost_capped_at_max(self):
        """提升后 confidence 不超过 MAX_CONFIDENCE。"""
        # 起始就接近上限
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.94)]
        sql = "FROM biz_orders JOIN biz_users"
        history = [sql] * 20  # 大量共现

        suggestions = mine_implicit_relationships(history, rels)
        boosted = suggestions[("biz_orders", "biz_users")]
        assert boosted <= MAX_CONFIDENCE
        assert boosted == pytest.approx(MAX_CONFIDENCE)

    def test_empty_inputs_return_empty(self):
        assert mine_implicit_relationships([], [_make_rel("a", "b")]) == {}
        assert mine_implicit_relationships(["SELECT 1"], []) == {}


# ── apply_feedback_signals ────────────────────────────────────

class TestApplyFeedback:
    """反馈回流：纠正下降、点赞提升。"""

    def test_correction_lowers_confidence(self):
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.8)]
        corrections = [("biz_orders", "biz_users")]

        suggestions = apply_feedback_signals(corrections, [], rels)

        assert ("biz_orders", "biz_users") in suggestions
        assert suggestions[("biz_orders", "biz_users")] == pytest.approx(0.8 - CORRECTION_PENALTY)

    def test_praise_raises_confidence(self):
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.6)]
        praises = [("biz_orders", "biz_users")]

        suggestions = apply_feedback_signals([], praises, rels)

        assert suggestions[("biz_orders", "biz_users")] == pytest.approx(0.6 + PRAISE_BOOST)

    def test_correction_floored_at_min(self):
        """纠正后不低于 MIN_CONFIDENCE。"""
        # 起始略高于 MIN，下降后正好触底
        rels = [_make_rel("biz_orders", "biz_users", confidence=MIN_CONFIDENCE + 0.05)]
        corrections = [("biz_orders", "biz_users")]

        suggestions = apply_feedback_signals(corrections, [], rels)
        assert suggestions[("biz_orders", "biz_users")] == pytest.approx(MIN_CONFIDENCE)

    def test_correction_at_min_produces_no_change(self):
        """已等于 MIN_CONFIDENCE 时不再下降（无变化 → 不产出建议）。"""
        rels = [_make_rel("biz_orders", "biz_users", confidence=MIN_CONFIDENCE)]
        corrections = [("biz_orders", "biz_users")]

        suggestions = apply_feedback_signals(corrections, [], rels)
        assert suggestions == {}

    def test_correction_takes_priority_over_praise(self):
        """同一表对既被纠正又被点赞 → 纠正优先（下降）。"""
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.7)]
        corrections = [("biz_orders", "biz_users")]
        praises = [("biz_orders", "biz_users")]

        suggestions = apply_feedback_signals(corrections, praises, rels)
        # 只应有纠正结果（下降），不应有提升
        assert suggestions[("biz_orders", "biz_users")] == pytest.approx(0.7 - CORRECTION_PENALTY)

    def test_unknown_pair_ignored(self):
        """反馈的表对不在已知关系里 → 忽略。"""
        rels = [_make_rel("biz_orders", "biz_users", confidence=0.7)]
        corrections = [("biz_orders", "biz_products")]  # 不是已知关系

        suggestions = apply_feedback_signals(corrections, [], rels)
        assert suggestions == {}

    def test_praise_capped_at_max(self):
        rels = [_make_rel("a", "b", confidence=MAX_CONFIDENCE - 0.01)]
        praises = [("a", "b")]

        suggestions = apply_feedback_signals([], praises, rels)
        assert suggestions[("a", "b")] == pytest.approx(MAX_CONFIDENCE)

    def test_empty_relationships_returns_empty(self):
        assert apply_feedback_signals([("a", "b")], [("c", "d")], []) == {}
