"""
E1 Task 2.1: persist_linkage_memory 测试

验证链路经验沉淀功能：
- 多表查询时创建 linkage 记忆
- 同表对重复查询时 co_occurrence+1
- 单表查询不创建 linkage
- 直接从 state 读取表对（不解析 SQL）
"""
import pytest
from unittest.mock import Mock
from app.core.agent_memory import AgentMemoryStore
from app.ai.recall import persist_linkage_memory


class TestPersistLinkageMemory:
    """链路经验沉淀测试"""

    def test_persist_creates_linkage_for_multi_table_query(self, tmp_path):
        """多表查询应创建 linkage 记忆"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        
        # 模拟 state（多表查询）
        state = Mock()
        state.current_tables = ["biz_orders", "biz_users"]
        state.join_path_section = "biz_orders.user_id = biz_users.id"
        state.thinking = Mock(
            tables=["biz_orders", "biz_users"],
            aggregation="COUNT",
            caveats=["注意 JOIN 条件"]
        )
        state.question = "查询每个用户的订单数"
        state.sql = "SELECT u.id, COUNT(o.id) FROM biz_users u JOIN biz_orders o ON u.id = o.user_id"
        
        # 执行沉淀
        persist_linkage_memory(store, state)
        
        # 验证：应创建 linkage 记忆
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 1
        
        # 验证字段
        link = linkage[0]
        assert set(link["tables"]) == {"biz_orders", "biz_users"}
        assert link["co_occurrence"] == 1
        # content 需要通过 read_memory 读取
        content = store.read_memory(link["id"])
        assert "biz_orders.user_id = biz_users.id" in content
        assert "查询每个用户的订单数" in content
        assert "COUNT" in content

    def test_persist_increments_co_occurrence_for_same_tables(self, tmp_path):
        """同表对重复查询应 co_occurrence+1"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        
        state = Mock()
        state.current_tables = ["biz_orders", "biz_users"]
        state.join_path_section = "biz_orders.user_id = biz_users.id"
        state.thinking = Mock(tables=["biz_orders", "biz_users"], aggregation="COUNT", caveats=[])
        state.question = "查询订单数"
        state.sql = "SELECT COUNT(*) FROM biz_orders JOIN biz_users"
        
        # 第一次
        persist_linkage_memory(store, state)
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 1
        assert linkage[0]["co_occurrence"] == 1
        
        # 第二次（同表对）
        persist_linkage_memory(store, state)
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 1  # 不创建新记忆
        assert linkage[0]["co_occurrence"] == 2  # co_occurrence+1

    def test_persist_skips_single_table_query(self, tmp_path):
        """单表查询不应创建 linkage"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        
        state = Mock()
        state.current_tables = ["biz_orders"]  # 单表
        state.join_path_section = ""
        state.thinking = Mock(tables=["biz_orders"], aggregation="COUNT", caveats=[])
        state.question = "查询订单总数"
        state.sql = "SELECT COUNT(*) FROM biz_orders"
        
        persist_linkage_memory(store, state)
        
        # 验证：不应创建 linkage
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 0

    def test_persist_handles_three_tables(self, tmp_path):
        """三表查询应创建 C(3,2)=3 个 linkage"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        
        state = Mock()
        state.current_tables = ["biz_orders", "biz_users", "biz_products"]
        state.join_path_section = "biz_orders.user_id = biz_users.id\nbiz_orders.product_id = biz_products.id"
        state.thinking = Mock(
            tables=["biz_orders", "biz_users", "biz_products"],
            aggregation="SUM",
            caveats=[]
        )
        state.question = "查询每个用户购买的商品总额"
        state.sql = "SELECT u.id, SUM(p.price) FROM ..."
        
        persist_linkage_memory(store, state)
        
        # 验证：应创建 3 个 linkage（orders-users, orders-products, users-products）
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 3
        
        # 验证每个 linkage 的表对
        table_pairs = [frozenset(m["tables"]) for m in linkage]
        assert frozenset(["biz_orders", "biz_users"]) in table_pairs
        assert frozenset(["biz_orders", "biz_products"]) in table_pairs
        assert frozenset(["biz_users", "biz_products"]) in table_pairs

    def test_persist_uses_state_fields_not_sql_parsing(self, tmp_path):
        """应从 state 直接读取表对，不解析 SQL"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))

        state = Mock()
        state.current_tables = ["biz_orders", "biz_users"]
        state.join_path_section = "biz_orders.user_id = biz_users.id"
        state.thinking = Mock(tables=["biz_orders", "biz_users"], aggregation="COUNT", caveats=[])
        state.question = "查询订单数"
        # SQL 里包含额外表（子查询），但 state.current_tables 只有 2 个
        state.sql = "SELECT * FROM biz_orders WHERE user_id IN (SELECT id FROM biz_users WHERE active=1)"

        persist_linkage_memory(store, state)

        # 验证：只创建 1 个 linkage（基于 state.current_tables，不解析 SQL）
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 1
        assert set(linkage[0]["tables"]) == {"biz_orders", "biz_users"}

    def test_persist_appends_new_scene_on_update(self, tmp_path):
        """W1 修复: 更新 linkage 时，不同 question 追加为新场景（去重）。"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))

        # 第一次查询：场景 A
        state1 = Mock()
        state1.current_tables = ["biz_orders", "biz_users"]
        state1.join_path_section = "biz_orders.user_id = biz_users.id"
        state1.thinking = Mock(tables=["biz_orders", "biz_users"], aggregation="COUNT", caveats=[])
        state1.question = "查询每个用户的订单数"
        state1.sql = "SELECT COUNT(*) FROM biz_orders JOIN biz_users"
        persist_linkage_memory(store, state1)

        # 第二次查询：场景 B（不同 question）
        state2 = Mock()
        state2.current_tables = ["biz_orders", "biz_users"]
        state2.join_path_section = "biz_orders.user_id = biz_users.id"
        state2.thinking = Mock(tables=["biz_orders", "biz_users"], aggregation="SUM", caveats=[])
        state2.question = "查询每个用户的总消费金额"
        state2.sql = "SELECT SUM(amount) FROM biz_orders JOIN biz_users"
        persist_linkage_memory(store, state2)

        # 第三次查询：重复场景 A（应去重，不追加）
        persist_linkage_memory(store, state1)

        # 验证：co_occurrence=3，但场景只有 2 个（A 和 B，A 重复不追加）
        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]
        assert len(linkage) == 1
        assert linkage[0]["co_occurrence"] == 3

        content = store.read_memory(linkage[0]["id"])
        assert "查询每个用户的订单数" in content
        assert "查询每个用户的总消费金额" in content

    def test_persist_marks_indirect_association_for_three_tables(self, tmp_path):
        """W2 修复: 三表查询时，无直接 JOIN 的表对标注「间接关联」。"""
        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))

        state = Mock()
        state.current_tables = ["biz_orders", "biz_users", "biz_products"]
        # 只有 orders-users 和 orders-products 有直接 JOIN，users-products 没有
        state.join_path_section = (
            "biz_orders.user_id = biz_users.id\n"
            "biz_orders.product_id = biz_products.id"
        )
        state.thinking = Mock(
            tables=["biz_orders", "biz_users", "biz_products"],
            aggregation="SUM",
            caveats=[]
        )
        state.question = "查询每个用户购买的商品总额"
        state.sql = "SELECT SUM(p.price) FROM ..."

        persist_linkage_memory(store, state)

        memories = store.list_memories()
        linkage = [m for m in memories if m.get("type") == "linkage"]

        # 应有 3 个 linkage
        assert len(linkage) == 3

        # 找到 users-products 的 linkage（无直接 JOIN）
        for link in linkage:
            content = store.read_memory(link["id"])
            tables = set(link["tables"])
            if tables == {"biz_users", "biz_products"}:
                # 应标注间接关联
                assert "间接关联" in content, f"users-products 缺少间接关联标注: {content}"
                assert "经由" in content or "biz_orders" in content
            elif tables == {"biz_orders", "biz_users"}:
                # 应有直接 JOIN 路径
                assert "JOIN 路径" in content
                assert "biz_orders.user_id = biz_users.id" in content
            elif tables == {"biz_orders", "biz_products"}:
                # 应有直接 JOIN 路径
                assert "JOIN 路径" in content
                assert "biz_orders.product_id = biz_products.id" in content
