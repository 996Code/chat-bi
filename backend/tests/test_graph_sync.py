"""
E1 Wave 3 单测: 图谱 confidence 更新 (linkage → 图谱同步)

覆盖:
  - linkage_memories_to_cooccurrence: 轻聚合
  - _compute_confidence_updates: 已知关系 boost
  - _discover_new_pairs: 新表对发现
  - apply_confidence_updates: 乐观锁 + 写入
  - sync_linkage_to_graph: 端到端同步
  - VersionConflictError: 冲突不静默
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.semantic_layer import (
    Model, Column, Relationship, SemanticModelContent,
)
from app.services.knowledge_graph import (
    VersionConflictError,
    linkage_memories_to_cooccurrence,
    _compute_confidence_updates,
    _discover_new_pairs,
    apply_confidence_updates,
    sync_linkage_to_graph,
)


# ── Fixtures ──────────────────────────────────────────────────

def _make_model(name: str, targets: list[tuple[str, float, str]] | None = None) -> dict:
    """构造语义层 model dict (用于 content JSON)。

    targets: [(target_model, confidence, source), ...]
    """
    rels = []
    for target, conf, source in (targets or []):
        rels.append({
            "name": f"{name}_to_{target}",
            "target_model": target,
            "join_type": "LEFT",
            "on": f"{name}.id = {target}.{name}_id",
            "type": "N:1",
            "source": source,
            "confidence": conf,
        })
    return {
        "name": name,
        "display_name": name,
        "columns": [{"name": "id", "display_name": "ID", "data_type": "INT", "source": "manual", "confidence": 1.0}],
        "relationships": rels,
        "metrics": [],
        "calculated_fields": [],
        "source": "manual",
        "confidence": 1.0,
    }


def _make_content(models: list[dict]) -> dict:
    """构造 SemanticModel content dict。"""
    return {"version": 1, "models": models, "sample_questions": []}


def _make_relationships(pairs: list[tuple[str, str, float, str]]) -> list[Relationship]:
    """构造 Relationship 对象列表。

    pairs: [(from_table, to_table, confidence, source), ...]
    """
    rels = []
    for from_t, to_t, conf, source in pairs:
        rels.append(Relationship(
            name=f"{from_t}_to_{to_t}",
            target_model=to_t,
            join_type="LEFT",
            on=f"{from_t}.id = {to_t}.{from_t}_id",
            type="N:1",
            source=source,
            confidence=conf,
        ))
    return rels


def _mock_mem_store(linkage_data: list[tuple[str, str, int]]) -> MagicMock:
    """构造 mock AgentMemoryStore。

    linkage_data: [(table_a, table_b, co_occurrence), ...]
    """
    store = MagicMock()
    memories = []
    for i, (a, b, co) in enumerate(linkage_data):
        memories.append({
            "id": f"linkage-{i}",
            "name": f"linkage-{a}-{b}",
            "type": "linkage",
            "co_occurrence": co,
            "tables": [a, b],
        })
    store.list_memories.return_value = memories
    return store


# ── TestLinkageMemoriesToCooccurrence ─────────────────────────

class TestLinkageMemoriesToCooccurrence:
    """轻聚合: 从 linkage 记忆 frontmatter 读 co_occurrence。"""

    def test_empty_store(self):
        store = _mock_mem_store([])
        result = linkage_memories_to_cooccurrence(store)
        assert result == {}

    def test_single_pair(self):
        store = _mock_mem_store([("orders", "users", 5)])
        result = linkage_memories_to_cooccurrence(store)
        assert result == {("orders", "users"): 5}

    def test_multiple_pairs(self):
        store = _mock_mem_store([
            ("orders", "users", 5),
            ("orders", "products", 3),
        ])
        result = linkage_memories_to_cooccurrence(store)
        assert result == {
            ("orders", "products"): 3,
            ("orders", "users"): 5,
        }

    def test_non_linkage_memories_ignored(self):
        """非 linkage 类型的记忆应被忽略。"""
        store = MagicMock()
        store.list_memories.return_value = [
            {"id": "p1", "name": "project-mem", "type": "project", "co_occurrence": 10, "tables": ["a", "b"]},
            {"id": "l1", "name": "linkage-a-b", "type": "linkage", "co_occurrence": 3, "tables": ["a", "b"]},
        ]
        result = linkage_memories_to_cooccurrence(store)
        assert result == {("a", "b"): 3}

    def test_malformed_linkage_skipped(self):
        """缺少 co_occurrence 或 tables 的 linkage 记忆应跳过。"""
        store = MagicMock()
        store.list_memories.return_value = [
            {"id": "l1", "name": "linkage-a-b", "type": "linkage", "co_occurrence": None, "tables": ["a", "b"]},
            {"id": "l2", "name": "linkage-c-d", "type": "linkage", "co_occurrence": 3, "tables": ["c"]},  # 只有一个表
        ]
        result = linkage_memories_to_cooccurrence(store)
        assert result == {}


# ── TestComputeConfidenceUpdates ──────────────────────────────

class TestComputeConfidenceUpdates:
    """已知关系 confidence boost 计算。"""

    def test_below_threshold_no_boost(self):
        """共现次数 < 阈值 → 不 boost。"""
        cooccurrence = {("orders", "users"): 2}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        updates = _compute_confidence_updates(cooccurrence, rels, co_occurrence_threshold=3, confidence_boost=0.1)
        assert updates == {}

    def test_at_threshold_boost(self):
        """共现次数 = 阈值 → boost 1 次。"""
        cooccurrence = {("orders", "users"): 3}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        updates = _compute_confidence_updates(cooccurrence, rels, co_occurrence_threshold=3, confidence_boost=0.1)
        assert updates == {("orders", "users"): 0.7}  # 0.6 + 0.1

    def test_above_threshold_multiple_boost(self):
        """共现次数 > 阈值 → boost 多次。"""
        cooccurrence = {("orders", "users"): 5}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        updates = _compute_confidence_updates(cooccurrence, rels, co_occurrence_threshold=3, confidence_boost=0.1)
        # boost_count = 5 - 3 + 1 = 3, new = 0.6 + 0.3 = 0.9
        assert updates == {("orders", "users"): 0.9}

    def test_capped_at_max_confidence(self):
        """confidence 封顶 0.95。"""
        cooccurrence = {("orders", "users"): 20}
        rels = _make_relationships([("orders", "users", 0.8, "ai_inferred")])
        updates = _compute_confidence_updates(cooccurrence, rels, co_occurrence_threshold=3, confidence_boost=0.1)
        # boost_count = 20 - 3 + 1 = 18, 0.8 + 1.8 = 2.6 → capped at 0.95
        assert updates == {("orders", "users"): 0.95}

    def test_unknown_pair_no_boost(self):
        """不在已知关系中的表对 → 不 boost (由新表对发现逻辑处理)。"""
        cooccurrence = {("orders", "categories"): 5}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        updates = _compute_confidence_updates(cooccurrence, rels, co_occurrence_threshold=3, confidence_boost=0.1)
        assert updates == {}

    def test_no_relationships(self):
        """无已知关系 → 无 boost。"""
        cooccurrence = {("orders", "users"): 5}
        updates = _compute_confidence_updates(cooccurrence, [], co_occurrence_threshold=3, confidence_boost=0.1)
        assert updates == {}


# ── TestDiscoverNewPairs ──────────────────────────────────────

class TestDiscoverNewPairs:
    """新表对发现: 共现频繁但不在现有关系中。"""

    def test_below_threshold_no_discovery(self):
        """共现 < new_pair_threshold → 不发现。"""
        cooccurrence = {("orders", "categories"): 3}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        new_pairs = _discover_new_pairs(cooccurrence, rels, new_pair_threshold=5)
        assert new_pairs == []

    def test_at_threshold_discovery(self):
        """共现 >= new_pair_threshold → 发现新表对。"""
        # linkage_memories_to_cooccurrence 返回字典序表对
        cooccurrence = {("categories", "orders"): 5}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        new_pairs = _discover_new_pairs(cooccurrence, rels, new_pair_threshold=5)
        assert len(new_pairs) == 1
        assert new_pairs[0] == (("categories", "orders"), 0.5)

    def test_existing_pair_not_discovered(self):
        """已在关系中的表对 → 不发现。"""
        cooccurrence = {("orders", "users"): 10}
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        new_pairs = _discover_new_pairs(cooccurrence, rels, new_pair_threshold=5)
        assert new_pairs == []

    def test_reverse_pair_not_discovered(self):
        """反向已在关系中的表对 → 不发现 (双向检查)。"""
        cooccurrence = {("users", "orders"): 10}  # 字典序: orders < users
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        new_pairs = _discover_new_pairs(cooccurrence, rels, new_pair_threshold=5)
        assert new_pairs == []

    def test_multiple_new_pairs(self):
        """多个新表对同时发现。"""
        cooccurrence = {
            ("categories", "orders"): 6,
            ("orders", "suppliers"): 7,
        }
        rels = _make_relationships([("orders", "users", 0.6, "name_pattern")])
        new_pairs = _discover_new_pairs(cooccurrence, rels, new_pair_threshold=5)
        assert len(new_pairs) == 2


# ── TestApplyConfidenceUpdates ────────────────────────────────

class TestApplyConfidenceUpdates:
    """乐观锁 + 写入语义层。"""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()

        # 模拟 current SemanticModel
        current_sm = MagicMock()
        current_sm.version = 1
        current_sm.content = _make_content([
            _make_model("biz_orders", [("biz_users", 0.6, "name_pattern")]),
            _make_model("biz_users"),
        ])
        current_sm.data_source_id = "ds-1"
        current_sm.tenant_id = "t-1"

        # 模拟 new SemanticModel
        new_sm = MagicMock()
        new_sm.version = 2
        new_sm.content = current_sm.content
        new_sm.data_source_id = "ds-1"
        new_sm.tenant_id = "t-1"

        # db.execute 返回链
        execute_results = []

        # 第 1 次: select current
        current_result = MagicMock()
        current_result.scalar_one_or_none.return_value = current_sm
        execute_results.append(current_result)

        # 第 2 次: select old_currents (same as current)
        old_currents_result = MagicMock()
        old_currents_result.scalars.return_value.all.return_value = [current_sm]
        execute_results.append(old_currents_result)

        # 第 3 次: select max version
        max_v_result = MagicMock()
        max_v_result.scalar_one.return_value = 1
        execute_results.append(max_v_result)

        db.execute = AsyncMock(side_effect=execute_results)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        # SemanticModel 构造 mock
        return db, current_sm, new_sm

    @pytest.mark.asyncio
    async def test_version_conflict_raises(self, mock_db):
        """乐观锁: expected_version != current.version → VersionConflictError。"""
        db, _, _ = mock_db
        with pytest.raises(VersionConflictError) as exc_info:
            await apply_confidence_updates(
                db=db,
                tenant_id="t-1",
                data_source_id="ds-1",
                updates={("biz_orders", "biz_users"): 0.8},
                expected_version=99,  # 冲突!
            )
        assert exc_info.value.expected_version == 99
        assert exc_info.value.current_version == 1

    @pytest.mark.asyncio
    async def test_no_current_version_raises(self):
        """无当前语义层版本 → ValueError。"""
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        with pytest.raises(ValueError, match="无当前语义层版本"):
            await apply_confidence_updates(
                db=db,
                tenant_id="t-1",
                data_source_id="ds-1",
                updates={("biz_orders", "biz_users"): 0.8},
            )

    @pytest.mark.asyncio
    async def test_no_updates_raises(self, mock_db):
        """无有效更新内容 → ValueError。"""
        db, _, _ = mock_db
        with pytest.raises(ValueError, match="无有效更新内容"):
            await apply_confidence_updates(
                db=db,
                tenant_id="t-1",
                data_source_id="ds-1",
                updates={},  # 空
            )

    @pytest.mark.asyncio
    async def test_successful_boost(self, mock_db):
        """成功 boost: confidence 更新 + 新版本写入。"""
        db, current_sm, _ = mock_db

        with patch("app.services.indexer_update.rebuild_index", new_callable=AsyncMock), \
             patch("app.services.embedder.get_embedder"), \
             patch("app.services.vector_store.get_vector_store"), \
             patch("app.services.knowledge_graph.SemanticModelContent"):
            new_version = await apply_confidence_updates(
                db=db,
                tenant_id="t-1",
                data_source_id="ds-1",
                updates={("biz_orders", "biz_users"): 0.8},
            )

        assert new_version == 2
        # 验证旧版本 is_current=False
        assert current_sm.is_current is False
        # 验证 db.add 被调用 (新版本)
        assert db.add.called
        assert db.commit.called

    @pytest.mark.asyncio
    async def test_new_pair_added(self, mock_db):
        """新表对发现: 新 Relationship 加入。"""
        db = AsyncMock()
        # 构造 content, 包含 biz_orders 和 biz_categories
        current_sm = MagicMock()
        current_sm.version = 1
        current_sm.content = _make_content([
            _make_model("biz_orders", [("biz_users", 0.6, "name_pattern")]),
            _make_model("biz_users"),
            _make_model("biz_categories"),
        ])
        current_sm.data_source_id = "ds-1"
        current_sm.tenant_id = "t-1"

        execute_results = []
        r1 = MagicMock()
        r1.scalar_one_or_none.return_value = current_sm
        execute_results.append(r1)
        r2 = MagicMock()
        r2.scalars.return_value.all.return_value = [current_sm]
        execute_results.append(r2)
        r3 = MagicMock()
        r3.scalar_one.return_value = 1
        execute_results.append(r3)
        db.execute = AsyncMock(side_effect=execute_results)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        new_pairs = [(("biz_categories", "biz_orders"), 0.5)]

        with patch("app.services.indexer_update.rebuild_index", new_callable=AsyncMock), \
             patch("app.services.embedder.get_embedder"), \
             patch("app.services.vector_store.get_vector_store"), \
             patch("app.services.knowledge_graph.SemanticModelContent"):
            new_version = await apply_confidence_updates(
                db=db,
                tenant_id="t-1",
                data_source_id="ds-1",
                updates={("biz_orders", "biz_users"): 0.8},
                new_pairs=new_pairs,
            )

        assert new_version == 2
        # 验证新关系被加入 biz_categories 的 relationships
        added_sm = db.add.call_args[0][0]
        categories_model = next(m for m in added_sm.content["models"] if m["name"] == "biz_categories")
        new_rel = next(r for r in categories_model["relationships"] if r["target_model"] == "biz_orders")
        assert new_rel["source"] == "implicit_mining"
        assert new_rel["confidence"] == 0.5


# ── TestSyncLinkageToGraph ────────────────────────────────────

class TestSyncLinkageToGraph:
    """端到端: linkage 记忆 → 图谱同步。"""

    @pytest.mark.asyncio
    async def test_no_linkage_memories(self):
        """无 linkage 记忆 → 跳过。"""
        db = AsyncMock()
        store = _mock_mem_store([])
        result = await sync_linkage_to_graph(db, store, "t-1", "ds-1")
        assert result["boosted_pairs"] == 0
        assert result["new_pairs"] == 0

    @pytest.mark.asyncio
    async def test_no_current_semantic_model(self):
        """无当前语义层 → 跳过。"""
        db = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=r)

        store = _mock_mem_store([("orders", "users", 5)])
        result = await sync_linkage_to_graph(db, store, "t-1", "ds-1")
        assert result["detail"] == "无当前语义层版本"

    @pytest.mark.asyncio
    async def test_below_threshold_no_update(self):
        """共现 < 阈值 → 无更新。"""
        db = AsyncMock()
        current_sm = MagicMock()
        current_sm.version = 1
        current_sm.content = _make_content([
            _make_model("biz_orders", [("biz_users", 0.6, "name_pattern")]),
            _make_model("biz_users"),
        ])
        r = MagicMock()
        r.scalar_one_or_none.return_value = current_sm
        db.execute = AsyncMock(return_value=r)

        store = _mock_mem_store([("biz_orders", "biz_users", 2)])  # 低于阈值 3
        result = await sync_linkage_to_graph(db, store, "t-1", "ds-1")
        assert result["boosted_pairs"] == 0
        assert result["detail"] == "无达阈值的表对, 无需更新"

    @pytest.mark.asyncio
    async def test_version_conflict_propagates(self):
        """乐观锁冲突 → VersionConflictError 传播 (不静默吞错)。"""
        db = AsyncMock()
        current_sm = MagicMock()
        current_sm.version = 5  # 当前版本 5
        current_sm.content = _make_content([
            _make_model("biz_orders", [("biz_users", 0.6, "name_pattern")]),
            _make_model("biz_users"),
        ])
        r = MagicMock()
        r.scalar_one_or_none.return_value = current_sm
        db.execute = AsyncMock(return_value=r)

        store = _mock_mem_store([("biz_orders", "biz_users", 5)])

        with pytest.raises(VersionConflictError):
            await sync_linkage_to_graph(
                db, store, "t-1", "ds-1",
                expected_version=1,  # 期望版本 1, 实际 5 → 冲突
            )
