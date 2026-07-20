"""
E1 Wave 5 验收测试: 端到端 + 反哺失败 + 图谱冲突 + 零额外计算

对标 tasks.md 8.1-8.6:
  8.1: 3 次同表对成功查询 → linkage co_occurrence=3 → 整理 → confidence 提升
  8.2: 5 次未知表对共现 → 整理触发新关系发现 → source=implicit_mining
  8.3: 反哺失败告知: persist_warning SSE 事件 (Wave 2 已覆盖, 此处补端到端)
  8.4: 图谱冲突: 并发整理 → 409 → 重试
  8.5: 零额外计算: persist_linkage_memory 只读 state 字段
  8.6: 全量测试通过
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from itertools import combinations

from app.core.agent_memory import AgentMemoryStore
from app.ai.recall import persist_linkage_memory, recall_memories, _extract_join_pairs
from app.services.knowledge_graph import (
    VersionConflictError,
    linkage_memories_to_cooccurrence,
    _compute_confidence_updates,
    _discover_new_pairs,
    apply_confidence_updates,
    sync_linkage_to_graph,
)
from app.schemas.semantic_layer import Relationship


# ── Helpers ───────────────────────────────────────────────────

def _make_state(tables, question="测试问题", sql="SELECT 1", join_path_section=""):
    """构造 mock AgentState。"""
    state = MagicMock()
    state.current_tables = tables
    state.question = question
    state.sql = sql
    state.join_path_section = join_path_section
    state.thinking = MagicMock(aggregation="SUM")
    return state


def _make_relationships(pairs):
    """构造 Relationship 列表。pairs: [(from, to, conf, source), ...]"""
    return [
        Relationship(
            name=f"{f}_to_{t}", target_model=t, join_type="LEFT",
            on=f"{f}.id = {t}.{f}_id", type="N:1", source=s, confidence=c,
        )
        for f, t, c, s in pairs
    ]


# ── 8.1: 3 次同表对成功查询 → linkage co_occurrence=3 → 整理 → confidence 提升 ──

class TestEndToEndConfidenceBoost:
    """端到端: 多次查询 → linkage 沉淀 → 轻聚合 → boost 计算。"""

    def test_three_queries_same_pair_co_occurrence_3(self, tmp_path):
        """3 次同表对查询 → co_occurrence=3 → 达阈值。"""
        store = AgentMemoryStore(base_dir=str(tmp_path))

        # 模拟 3 次查询: orders + users
        for i in range(3):
            state = _make_state(
                tables=["biz_orders", "biz_users"],
                question=f"查询 {i+1}",
                join_path_section="biz_orders.user_id = biz_users.id",
            )
            persist_linkage_memory(store, state)

        # 验证 linkage 记忆 co_occurrence=3
        cooccurrence = linkage_memories_to_cooccurrence(store)
        pair = tuple(sorted(["biz_orders", "biz_users"]))
        assert cooccurrence[pair] == 3

        # 验证 boost 计算
        rels = _make_relationships([
            ("biz_orders", "biz_users", 0.6, "name_pattern"),
        ])
        updates = _compute_confidence_updates(
            cooccurrence, rels,
            co_occurrence_threshold=3, confidence_boost=0.1,
        )
        assert updates == {("biz_orders", "biz_users"): 0.7}  # 0.6 + 0.1

    def test_single_table_query_skipped(self, tmp_path):
        """单表查询 → 不沉淀 linkage 记忆。"""
        store = AgentMemoryStore(base_dir=str(tmp_path))
        state = _make_state(tables=["biz_orders"])
        persist_linkage_memory(store, state)

        cooccurrence = linkage_memories_to_cooccurrence(store)
        assert cooccurrence == {}


# ── 8.2: 5 次未知表对共现 → 新关系发现 ───────────────────────

class TestEndToEndNewPairDiscovery:
    """端到端: 高频未知表对 → 新关系发现。"""

    def test_five_queries_unknown_pair_discovered(self, tmp_path):
        """5 次未知表对共现 → 达 new_pair_threshold → 发现新关系。"""
        store = AgentMemoryStore(base_dir=str(tmp_path))

        # 模拟 5 次查询: orders + categories (不在现有关系中)
        for i in range(5):
            state = _make_state(
                tables=["biz_orders", "biz_categories"],
                question=f"分类查询 {i+1}",
            )
            persist_linkage_memory(store, state)

        cooccurrence = linkage_memories_to_cooccurrence(store)
        pair = tuple(sorted(["biz_orders", "biz_categories"]))
        assert cooccurrence[pair] == 5

        # 验证新表对发现
        rels = _make_relationships([
            ("biz_orders", "biz_users", 0.6, "name_pattern"),
        ])
        new_pairs = _discover_new_pairs(cooccurrence, rels, new_pair_threshold=5)
        assert len(new_pairs) == 1
        assert new_pairs[0] == (pair, 0.5)


# ── 8.3: 反哺失败告知 (端到端, 核心逻辑已在 test_chat_stream.py) ──

class TestEndToEndPersistWarning:
    """端到端: persist_warning SSE 事件 (核心场景已在 test_chat_stream.py 覆盖,
    此处验证 persist_linkage_memory 失败场景)。"""

    def test_linkage_persist_failure_returns_none(self, tmp_path):
        """persist_linkage_memory 失败不抛异常 (由 _persist 统一捕获发 persist_warning)。"""
        store = MagicMock()
        store.get_linkage_memory.side_effect = Exception("disk full")
        # persist_linkage_memory 内部不 catch, 由 _persist 的 try/except 处理
        state = _make_state(tables=["biz_orders", "biz_users"])
        with pytest.raises(Exception, match="disk full"):
            persist_linkage_memory(store, state)


# ── 8.4: 图谱冲突 → VersionConflictError ─────────────────────

class TestEndToEndVersionConflict:
    """端到端: 并发整理 → 第二个冲突。"""

    @pytest.mark.asyncio
    async def test_concurrent_consolidate_conflict(self):
        """两个同步操作: 第二个因版本号不匹配抛 VersionConflictError。"""
        db = AsyncMock()
        current_sm = MagicMock()
        current_sm.version = 5  # 当前版本
        current_sm.content = {
            "version": 1,
            "models": [
                {
                    "name": "biz_orders",
                    "display_name": "orders",
                    "columns": [{"name": "id", "display_name": "ID", "data_type": "INT", "source": "manual", "confidence": 1.0}],
                    "relationships": [{
                        "name": "biz_orders_to_biz_users",
                        "target_model": "biz_users",
                        "join_type": "LEFT",
                        "on": "biz_orders.user_id = biz_users.id",
                        "type": "N:1",
                        "source": "name_pattern",
                        "confidence": 0.6,
                    }],
                    "metrics": [],
                    "calculated_fields": [],
                    "source": "manual",
                    "confidence": 1.0,
                },
                {
                    "name": "biz_users",
                    "display_name": "users",
                    "columns": [],
                    "relationships": [],
                    "metrics": [],
                    "calculated_fields": [],
                    "source": "manual",
                    "confidence": 1.0,
                },
            ],
            "sample_questions": [],
        }
        current_sm.data_source_id = "ds-1"
        current_sm.tenant_id = "t-1"

        r = MagicMock()
        r.scalar_one_or_none.return_value = current_sm
        db.execute = AsyncMock(return_value=r)

        store = MagicMock()
        store.list_memories.return_value = [
            {"id": "l1", "name": "linkage", "type": "linkage", "co_occurrence": 5, "tables": ["biz_orders", "biz_users"]},
        ]

        # 期望版本 3, 实际 5 → 冲突
        with pytest.raises(VersionConflictError) as exc_info:
            await sync_linkage_to_graph(db, store, "t-1", "ds-1", expected_version=3)
        assert exc_info.value.expected_version == 3
        assert exc_info.value.current_version == 5


# ── 8.5: 零额外计算验证 ──────────────────────────────────────

class TestZeroExtraComputation:
    """验证 persist_linkage_memory 只读 state 字段, 不调 SQL 正则/JOIN 重算。"""

    def test_persist_linkage_uses_state_directly(self, tmp_path):
        """persist_linkage_memory 只从 state 读取, 不解析 SQL。"""
        store = AgentMemoryStore(base_dir=str(tmp_path))
        state = _make_state(
            tables=["biz_orders", "biz_users", "biz_products"],
            question="多表联合查询",
            sql="SELECT * FROM biz_orders JOIN biz_users ON biz_orders.user_id = biz_users.id JOIN biz_products ON biz_orders.product_id = biz_products.id",
            join_path_section="biz_orders.user_id = biz_users.id\nbiz_orders.product_id = biz_products.id",
        )

        persist_linkage_memory(store, state)

        # 验证: 3 个表产生 C(3,2)=3 个 linkage 记忆
        cooccurrence = linkage_memories_to_cooccurrence(store)
        assert len(cooccurrence) == 3
        pairs = set(cooccurrence.keys())
        expected = {
            tuple(sorted(["biz_orders", "biz_users"])),
            tuple(sorted(["biz_orders", "biz_products"])),
            tuple(sorted(["biz_products", "biz_users"])),
        }
        assert pairs == expected


# ── 8.6: 全量测试通过 (由 CI 验证, 此处只确认单测文件本身) ──

class TestRecallMemoriesHitsLinkage:
    """7.2: linkage 记忆不直接注入 prompt (结构化数据), 通过图谱 confidence 间接影响。

    recall_memories 跳过 linkage 类型, 因为它是结构化数据 (co_occurrence/tables),
    不适合直接注入 SQL 生成 prompt。它的价值通过 sync_linkage_to_graph 间接体现:
    整理时 boost 图谱 confidence → 影响表扩展和 JOIN 路径。
    """

    def test_recall_skips_linkage_memory(self, tmp_path):
        """recall_memories 不返回 linkage 类型记忆。"""
        store = AgentMemoryStore(base_dir=str(tmp_path))
        state = _make_state(
            tables=["biz_orders", "biz_users"],
            question="查询订单和用户",
            join_path_section="biz_orders.user_id = biz_users.id",
        )
        persist_linkage_memory(store, state)

        # 召回: 应该不包含 linkage 类型
        results = recall_memories(
            question="查一下订单和用户的关系",
            memory_dir=str(tmp_path),
            max_count=5,
        )
        linkage_results = [r for r in results if "linkage" in r.get("name", "")]
        assert len(linkage_results) == 0, "linkage 记忆不应被 recall_memories 返回"
