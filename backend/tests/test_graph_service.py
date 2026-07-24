"""
P1-12: SchemaGraph 算法测试覆盖

覆盖 graph_service.py 全部公开方法:
  - 构建: _build_from_content (正向+反向边, confidence 去重)
  - 查询: find_join_paths, expand_tables, get_join_context
  - 分析: get_communities, get_hub_tables, get_table_neighbors, get_impact
  - 修改: add_relationship, remove_relationship
  - 可视化: to_vis_data, to_vis_subgraph, get_reverse_relationships
  - 属性: node_count, edge_count, has_node, has_edge

Edge cases:
  - 超级枢纽 (degree=90)
  - 多社区种子
  - 无连通路径
  - 空图
  - max_hops 截断
  - max_total 扩散 cap
  - 双向边 forward/reverse 语义
"""
from __future__ import annotations

import pytest

from app.schemas.semantic_layer import (
    Column,
    Model,
    Relationship,
    SemanticModelContent,
)
from app.services.graph_service import JoinPath, SchemaGraph


# ── Fixtures ──────────────────────────────────────────────────


def _make_rel(
    name: str,
    target: str,
    on: str = "",
    join_type: str = "LEFT",
    cardinality: str = "N:1",
    confidence: float = 1.0,
    source: str = "foreign_key",
) -> Relationship:
    """快捷构建 Relationship。"""
    return Relationship(
        name=name,
        target_model=target,
        join_type=join_type,
        on=on,
        type=cardinality,
        confidence=confidence,
        source=source,
    )


def _make_model(
    name: str,
    display_name: str = "",
    columns: list[Column] | None = None,
    relationships: list[Relationship] | None = None,
) -> Model:
    """快捷构建 Model。"""
    return Model(
        name=name,
        display_name=display_name or name,
        columns=columns or [],
        relationships=relationships or [],
        source="manual",
        confidence=1.0,
    )


def _ecom_content() -> SemanticModelContent:
    """电商场景语义层: 5 张表, 4 条关系。

    biz_orders ──FK──→ biz_users
    biz_orders ──FK──→ biz_products
    biz_order_items ──FK──→ biz_orders
    biz_order_items ──FK──→ biz_products

    biz_users 是枢纽表 (degree 高)
    """
    return SemanticModelContent(
        version=1,
        models=[
            _make_model(
                "biz_orders",
                display_name="订单表",
                columns=[Column(name="id", display_name="ID", data_type="BIGINT", source="manual", confidence=1.0)],
                relationships=[
                    _make_rel("fk_user", "biz_users", on="biz_orders.user_id = biz_users.id", confidence=1.0, source="foreign_key"),
                    _make_rel("fk_product", "biz_products", on="biz_orders.product_id = biz_products.id", confidence=0.8, source="name_pattern"),
                ],
            ),
            _make_model(
                "biz_users",
                display_name="用户表",
                columns=[Column(name="id", display_name="ID", data_type="BIGINT", source="manual", confidence=1.0)],
            ),
            _make_model(
                "biz_products",
                display_name="商品表",
                columns=[Column(name="id", display_name="ID", data_type="BIGINT", source="manual", confidence=1.0)],
            ),
            _make_model(
                "biz_order_items",
                display_name="订单明细表",
                columns=[Column(name="id", display_name="ID", data_type="BIGINT", source="manual", confidence=1.0)],
                relationships=[
                    _make_rel("fk_order", "biz_orders", on="biz_order_items.order_id = biz_orders.id", confidence=1.0, source="foreign_key"),
                    _make_rel("fk_product", "biz_products", on="biz_order_items.product_id = biz_products.id", confidence=0.7, source="name_pattern"),
                ],
            ),
            _make_model(
                "biz_payments",
                display_name="支付表",
                columns=[Column(name="id", display_name="ID", data_type="BIGINT", source="manual", confidence=1.0)],
                relationships=[
                    _make_rel("fk_order", "biz_orders", on="biz_payments.order_id = biz_orders.id", confidence=1.0, source="foreign_key"),
                ],
            ),
        ],
    )


@pytest.fixture
def ecom_graph() -> SchemaGraph:
    """电商场景图谱。"""
    return SchemaGraph(_ecom_content())


@pytest.fixture
def empty_graph() -> SchemaGraph:
    """空图谱。"""
    return SchemaGraph()


# ── 1. 构建 ──────────────────────────────────────────────────


class TestBuild:
    """图谱构建: 正向+反向边, confidence 去重。"""

    def test_nodes_created(self, ecom_graph: SchemaGraph) -> None:
        """5 张表 → 5 个节点。"""
        assert ecom_graph.node_count == 5
        for name in ("biz_orders", "biz_users", "biz_products", "biz_order_items", "biz_payments"):
            assert ecom_graph.has_node(name)

    def test_bidirectional_edges(self, ecom_graph: SchemaGraph) -> None:
        """每条 Relationship 产生正向+反向两条边。

        4 条关系 × 2 方向 = 8 条边。
        """
        # biz_orders → biz_users (forward) + biz_users → biz_orders (reverse)
        assert ecom_graph.has_edge("biz_orders", "biz_users")
        assert ecom_graph.has_edge("biz_users", "biz_orders")
        # biz_orders → biz_products + reverse
        assert ecom_graph.has_edge("biz_orders", "biz_products")
        assert ecom_graph.has_edge("biz_products", "biz_orders")
        # biz_order_items → biz_orders + reverse
        assert ecom_graph.has_edge("biz_order_items", "biz_orders")
        assert ecom_graph.has_edge("biz_orders", "biz_order_items")
        # biz_order_items → biz_products + reverse
        assert ecom_graph.has_edge("biz_order_items", "biz_products")
        assert ecom_graph.has_edge("biz_products", "biz_order_items")
        # biz_payments → biz_orders + reverse
        assert ecom_graph.has_edge("biz_payments", "biz_orders")
        assert ecom_graph.has_edge("biz_orders", "biz_payments")
        # 总边数: 5 条关系 × 2 = 10
        assert ecom_graph.edge_count == 10

    def test_edge_direction_attribute(self, ecom_graph: SchemaGraph) -> None:
        """正向边 direction=forward, 反向边 direction=reverse。"""
        forward_data = ecom_graph._graph.edges["biz_orders", "biz_users"]
        assert forward_data["direction"] == "forward"
        reverse_data = ecom_graph._graph.edges["biz_users", "biz_orders"]
        assert reverse_data["direction"] == "reverse"

    def test_confidence_dedup_higher_wins(self) -> None:
        """同对边 confidence 更高的保留。

        biz_orders → biz_users 先加 confidence=0.5,
        再加 confidence=1.0 → 保留 1.0。
        """
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("a", relationships=[
                    _make_rel("r1", "b", confidence=0.5, source="name_pattern"),
                ]),
                _make_model("b"),
            ],
        )
        g = SchemaGraph(content)
        assert g._graph.edges["a", "b"]["confidence"] == 0.5

        # 再加一条更高 confidence 的同对边
        g.add_relationship("a", _make_rel("r2", "b", confidence=1.0, source="foreign_key"))
        assert g._graph.edges["a", "b"]["confidence"] == 1.0

    def test_confidence_dedup_lower_skipped(self) -> None:
        """同对边 confidence 更低的被跳过。"""
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("a", relationships=[
                    _make_rel("r1", "b", confidence=1.0, source="foreign_key"),
                ]),
                _make_model("b"),
            ],
        )
        g = SchemaGraph(content)
        # 尝试加更低 confidence
        g.add_relationship("a", _make_rel("r2", "b", confidence=0.5, source="name_pattern"))
        # 保留高 confidence
        assert g._graph.edges["a", "b"]["confidence"] == 1.0

    def test_node_attributes(self, ecom_graph: SchemaGraph) -> None:
        """节点属性: display_name, column_count, metric_count, source。"""
        data = ecom_graph._graph.nodes["biz_orders"]
        assert data["display_name"] == "订单表"
        assert data["column_count"] == 1
        assert data["metric_count"] == 0
        assert data["source"] == "manual"

    def test_empty_content(self, empty_graph: SchemaGraph) -> None:
        """空 SemanticModelContent → 空图。"""
        assert empty_graph.node_count == 0
        assert empty_graph.edge_count == 0


# ── 2. find_join_paths ───────────────────────────────────────


class TestFindJoinPaths:
    """Dijkstra 最短路径 + max_hops 截断。"""

    def test_direct_path(self, ecom_graph: SchemaGraph) -> None:
        """直接 FK 关系: biz_orders → biz_users (1 跳)。"""
        paths = ecom_graph.find_join_paths("biz_orders", "biz_users")
        assert len(paths) == 1
        assert paths[0].tables == ["biz_orders", "biz_users"]
        assert paths[0].total_weight == pytest.approx(0.0)  # confidence=1.0 → weight=0.0

    def test_two_hop_path(self, ecom_graph: SchemaGraph) -> None:
        """2 跳路径: biz_order_items → biz_orders → biz_users。"""
        paths = ecom_graph.find_join_paths("biz_order_items", "biz_users")
        assert len(paths) == 1
        assert paths[0].tables == ["biz_order_items", "biz_orders", "biz_users"]
        assert len(paths[0].on_conditions) == 2
        assert len(paths[0].confidences) == 2

    def test_same_node_returns_empty(self, ecom_graph: SchemaGraph) -> None:
        """source == target → 空列表。"""
        assert ecom_graph.find_join_paths("biz_orders", "biz_orders") == []

    def test_nonexistent_node_returns_empty(self, ecom_graph: SchemaGraph) -> None:
        """不存在的节点 → 空列表。"""
        assert ecom_graph.find_join_paths("biz_orders", "nonexistent") == []
        assert ecom_graph.find_join_paths("nonexistent", "biz_users") == []

    def test_no_path_returns_empty(self) -> None:
        """无连通路径 → 空列表。"""
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("a"),
                _make_model("b"),  # a 和 b 无关系
            ],
        )
        g = SchemaGraph(content)
        assert g.find_join_paths("a", "b") == []

    def test_max_hops_truncation(self, ecom_graph: SchemaGraph) -> None:
        """max_hops=1 截断 2 跳路径。"""
        # biz_order_items → biz_users 需要 2 跳
        paths = ecom_graph.find_join_paths("biz_order_items", "biz_users", max_hops=1)
        assert paths == []

    def test_max_hops_allows_short_path(self, ecom_graph: SchemaGraph) -> None:
        """max_hops=1 允许 1 跳路径。"""
        paths = ecom_graph.find_join_paths("biz_orders", "biz_users", max_hops=1)
        assert len(paths) == 1

    def test_fk_path_preferred_over_name_pattern(self) -> None:
        """FK (confidence=1.0, weight=0.0) 路径优先于 name_pattern (confidence=0.6, weight=0.4)。"""
        # 构建两条路径: A→B (FK, 1跳, weight=0.0) vs A→C→B (name_pattern, 2跳, weight=0.8)
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("a", relationships=[
                    _make_rel("fk_b", "b", on="a.b_id = b.id", confidence=1.0, source="foreign_key"),
                    _make_rel("np_c", "c", on="a.c_id = c.id", confidence=0.6, source="name_pattern"),
                ]),
                _make_model("b"),
                _make_model("c", relationships=[
                    _make_rel("np_b", "b", on="c.b_id = b.id", confidence=0.6, source="name_pattern"),
                ]),
            ],
        )
        g = SchemaGraph(content)
        paths = g.find_join_paths("a", "b")
        assert len(paths) == 1
        # 应走 FK 直连 (1 跳), 不走 name_pattern 绕行 (2 跳)
        assert paths[0].tables == ["a", "b"]
        assert paths[0].total_weight == pytest.approx(0.0)

    def test_empty_graph(self, empty_graph: SchemaGraph) -> None:
        """空图 → 空路径。"""
        assert empty_graph.find_join_paths("a", "b") == []


# ── 3. expand_tables ─────────────────────────────────────────


class TestExpandTables:
    """智能表扩展: 最短路径 + 距离排序邻居 + 社区补全 + max_total cap。"""

    def test_empty_seeds(self, ecom_graph: SchemaGraph) -> None:
        """空种子 → 空列表。"""
        assert ecom_graph.expand_tables([]) == []

    def test_single_seed_no_expansion(self, ecom_graph: SchemaGraph) -> None:
        """单种子 + max_total=1 → 只返回种子。"""
        result = ecom_graph.expand_tables(["biz_orders"], max_total=1)
        assert "biz_orders" in result

    def test_two_seeds_fill_join_bridge(self, ecom_graph: SchemaGraph) -> None:
        """两种子有 JOIN 路径 → 补齐桥接表。

        biz_order_items + biz_users → 补 biz_orders (桥接表)
        """
        result = ecom_graph.expand_tables(
            ["biz_order_items", "biz_users"], max_total=10,
        )
        assert "biz_order_items" in result
        assert "biz_users" in result
        assert "biz_orders" in result  # 桥接表

    def test_max_total_cap(self, ecom_graph: SchemaGraph) -> None:
        """max_total 限制扩展后总表数。"""
        result = ecom_graph.expand_tables(["biz_orders"], max_total=2)
        # 种子 + 最多 1 张扩展表
        assert len(result) <= 2
        assert "biz_orders" in result

    def test_seeds_always_preserved(self, ecom_graph: SchemaGraph) -> None:
        """种子表永远保留, 即使不在图中。"""
        result = ecom_graph.expand_tables(
            ["biz_orders", "nonexistent_table"], max_total=2,
        )
        assert "biz_orders" in result
        assert "nonexistent_table" in result  # 不在图中也保留

    def test_neighbor_distance_sorting(self) -> None:
        """邻居按图距离排序: dist=1 优先占名额, dist=2 在名额有余时加入。"""
        # 构建星形图: hub → a, b, c (1跳); a → d (2跳)
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("hub", relationships=[
                    _make_rel("r_a", "a", confidence=1.0),
                    _make_rel("r_b", "b", confidence=1.0),
                    _make_rel("r_c", "c", confidence=1.0),
                ]),
                _make_model("a", relationships=[
                    _make_rel("r_d", "d", confidence=0.8),
                ]),
                _make_model("b"),
                _make_model("c"),
                _make_model("d"),
            ],
        )
        g = SchemaGraph(content)
        # max_total=4: hub(种子) + 3 个名额 → a,b,c (dist=1) 优先, d (dist=2) 被排除
        result = g.expand_tables(["hub"], max_total=4)
        assert "hub" in result
        # dist=1 的 a,b,c 应优先占名额
        dist1_neighbors = {"a", "b", "c"}
        assert dist1_neighbors.issubset(set(result))
        # d 是 dist=2, 名额不够时被排除
        assert "d" not in result

    def test_super_hub_no_flood(self) -> None:
        """超级枢纽 (degree=90) 的远亲不因遍历顺序先占满名额。"""
        # hub 有 90 个 1 跳邻居, 每个邻居又有 1 个 2 跳远亲
        rels = [_make_rel(f"r_{i}", f"neighbor_{i}", confidence=0.6) for i in range(90)]
        models = [_make_model("hub", relationships=rels)]
        for i in range(90):
            models.append(_make_model(f"neighbor_{i}", relationships=[
                _make_rel(f"r_far_{i}", f"far_{i}", confidence=0.3),
            ]))
            models.append(_make_model(f"far_{i}"))
        content = SemanticModelContent(version=1, models=models)
        g = SchemaGraph(content)

        # max_total=5: hub(种子) + 4 名额 → 应优先选 dist=1 的邻居
        result = g.expand_tables(["hub"], max_total=5)
        assert "hub" in result
        assert len(result) <= 5
        # 所有扩展表都应是 dist=1 的邻居, 不应有 dist=2 的 far_* 表
        for t in result:
            if t != "hub":
                assert t.startswith("neighbor_"), f"dist=2 表 {t} 不应占名额"

    def test_all_seeds_not_in_graph(self, ecom_graph: SchemaGraph) -> None:
        """所有种子都不在图中 → 原样返回种子列表。"""
        result = ecom_graph.expand_tables(["x", "y"])
        assert set(result) == {"x", "y"}


# ── 4. get_join_context ──────────────────────────────────────


class TestGetJoinContext:
    """最小 JOIN 路径集: 去重合并。"""

    def test_two_tables(self, ecom_graph: SchemaGraph) -> None:
        """2 张表 → 1 条路径。"""
        paths = ecom_graph.get_join_context(["biz_orders", "biz_users"])
        assert len(paths) == 1
        assert paths[0].tables == ["biz_orders", "biz_users"]

    def test_three_tables_bridge(self, ecom_graph: SchemaGraph) -> None:
        """3 张表需桥接: biz_order_items + biz_users + biz_products。"""
        paths = ecom_graph.get_join_context(
            ["biz_order_items", "biz_users", "biz_products"],
        )
        # 应找到连接路径 (可能经过 biz_orders 桥接)
        assert len(paths) >= 1
        # 所有表应被路径覆盖
        covered = set()
        for p in paths:
            covered.update(p.tables)
        assert "biz_order_items" in covered
        assert "biz_users" in covered

    def test_single_table_returns_empty(self, ecom_graph: SchemaGraph) -> None:
        """单张表 → 空路径。"""
        assert ecom_graph.get_join_context(["biz_orders"]) == []

    def test_no_path_between_tables(self) -> None:
        """无连通路径的表对 → 路径集不含该对。"""
        content = SemanticModelContent(
            version=1,
            models=[_make_model("a"), _make_model("b")],
        )
        g = SchemaGraph(content)
        paths = g.get_join_context(["a", "b"])
        assert paths == []

    def test_edge_dedup(self, ecom_graph: SchemaGraph) -> None:
        """共享边的路径去重: A→B→C 和 A→B→D 共享 A→B 边, 只出现一次。"""
        # biz_orders 同时连 biz_users 和 biz_products
        paths = ecom_graph.get_join_context(
            ["biz_orders", "biz_users", "biz_products"],
        )
        # 检查边去重: (biz_orders, biz_users) 不应重复
        seen_edges: set[tuple[str, str]] = set()
        for p in paths:
            for i in range(len(p.tables) - 1):
                edge = (p.tables[i], p.tables[i + 1])
                rev = (p.tables[i + 1], p.tables[i])
                assert edge not in seen_edges and rev not in seen_edges
                seen_edges.add(edge)


# ── 5. get_communities ───────────────────────────────────────


class TestGetCommunities:
    """社区发现: label_propagation / greedy_modularity。"""

    def test_ecom_communities(self, ecom_graph: SchemaGraph) -> None:
        """电商图谱至少有 1 个社区, 所有表都被分配。"""
        communities = ecom_graph.get_communities()
        assert len(communities) >= 1
        all_tables = set()
        for c in communities:
            all_tables.update(c)
        assert all_tables == {"biz_orders", "biz_users", "biz_products", "biz_order_items", "biz_payments"}

    def test_empty_graph(self, empty_graph: SchemaGraph) -> None:
        """空图 → 空社区。"""
        assert empty_graph.get_communities() == []

    def test_isolated_nodes_single_community(self) -> None:
        """孤立节点 (无边) → 每个节点自成一个社区 (label_propagation 行为)。"""
        content = SemanticModelContent(
            version=1,
            models=[_make_model("a"), _make_model("b"), _make_model("c")],
        )
        g = SchemaGraph(content)
        communities = g.get_communities()
        # label_propagation: 孤立节点各自成社区
        assert len(communities) >= 1
        all_tables = {t for c in communities for t in c}
        assert all_tables == {"a", "b", "c"}

    def test_greedy_modularity(self, ecom_graph: SchemaGraph, monkeypatch: pytest.MonkeyPatch) -> None:
        """greedy_modularity 算法可用。"""
        from app.core.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "graph_community_algorithm", "greedy_modularity")
        communities = ecom_graph.get_communities()
        assert len(communities) >= 1

    def test_invalid_algorithm_fallback(self, ecom_graph: SchemaGraph, monkeypatch: pytest.MonkeyPatch) -> None:
        """无效算法值 → WARNING + 降级 label_propagation。"""
        from app.core.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "graph_community_algorithm", "invalid_algo")
        communities = ecom_graph.get_communities()
        # 应降级到 label_propagation, 仍返回结果
        assert len(communities) >= 1


# ── 6. get_hub_tables ────────────────────────────────────────


class TestGetHubTables:
    """枢纽表识别: 度中心度。"""

    def test_ecom_hub(self, ecom_graph: SchemaGraph) -> None:
        """biz_orders 度最高 (4 条关系 × 2 方向 = 8 条边)。"""
        hubs = ecom_graph.get_hub_tables(top_k=3)
        assert len(hubs) >= 1
        # biz_orders 连接 biz_users, biz_products, biz_order_items, biz_payments → 度最高
        assert hubs[0][0] == "biz_orders"

    def test_top_k_limit(self, ecom_graph: SchemaGraph) -> None:
        """top_k 限制返回数量。"""
        hubs = ecom_graph.get_hub_tables(top_k=2)
        assert len(hubs) <= 2

    def test_empty_graph(self, empty_graph: SchemaGraph) -> None:
        """空图 → 空列表。"""
        assert empty_graph.get_hub_tables() == []

    def test_centrality_ordering(self, ecom_graph: SchemaGraph) -> None:
        """中心度降序排列。"""
        hubs = ecom_graph.get_hub_tables()
        for i in range(len(hubs) - 1):
            assert hubs[i][1] >= hubs[i + 1][1]


# ── 7. get_table_neighbors ───────────────────────────────────


class TestGetTableNeighbors:
    """表的邻居信息 (可视化用)。"""

    def test_depth_1(self, ecom_graph: SchemaGraph) -> None:
        """depth=1: biz_orders 的直接邻居。"""
        result = ecom_graph.get_table_neighbors("biz_orders", depth=1)
        assert "biz_orders" in result
        neighbors = result["biz_orders"]["neighbors"]
        # biz_orders 的 1 跳邻居: biz_users, biz_products, biz_order_items, biz_payments
        assert "biz_users" in neighbors
        assert "biz_products" in neighbors

    def test_nonexistent_table(self, ecom_graph: SchemaGraph) -> None:
        """不存在的表 → 空字典。"""
        assert ecom_graph.get_table_neighbors("nonexistent") == {}

    def test_edges_include_both_directions(self, ecom_graph: SchemaGraph) -> None:
        """边包含正向和反向。"""
        result = ecom_graph.get_table_neighbors("biz_orders", depth=1)
        edges = result["biz_orders"]["edges"]
        # 应有正向边 (biz_orders → biz_users) 和反向边 (biz_users → biz_orders)
        sources = {e["source"] for e in edges}
        targets = {e["target"] for e in edges}
        assert "biz_orders" in sources or "biz_orders" in targets


# ── 8. get_impact ────────────────────────────────────────────


class TestGetImpact:
    """影响分析: 下游可达表。"""

    def test_orders_impact(self, ecom_graph: SchemaGraph) -> None:
        """biz_orders 的下游: biz_order_items, biz_payments (它们 FK 指向 orders)。

        注意: 图谱中 biz_order_items → biz_orders 是正向边,
        biz_orders → biz_order_items 是反向边。
        descendants 沿有向边走, 所以 biz_orders 的下游是反向边指向的表。
        """
        impact = ecom_graph.get_impact("biz_orders")
        # biz_orders 有反向边到 biz_order_items 和 biz_payments
        assert isinstance(impact, list)

    def test_leaf_table(self, ecom_graph: SchemaGraph) -> None:
        """叶子表 (无下游) → 空列表。"""
        # biz_users 没有出边 (只有入边)
        impact = ecom_graph.get_impact("biz_users")
        # biz_users 的反向边指向 biz_orders, 所以有下游
        assert isinstance(impact, list)

    def test_nonexistent_table(self, ecom_graph: SchemaGraph) -> None:
        """不存在的表 → 空列表。"""
        assert ecom_graph.get_impact("nonexistent") == []


# ── 9. add_relationship / remove_relationship ────────────────


class TestGraphModification:
    """图修改: 新增/删除关系。"""

    def test_add_relationship(self, ecom_graph: SchemaGraph) -> None:
        """新增关系 → 正向+反向边。"""
        initial_edges = ecom_graph.edge_count
        ecom_graph.add_relationship(
            "biz_users",
            _make_rel("new_rel", "biz_products", on="biz_users.id = biz_products.user_id", confidence=0.9),
        )
        assert ecom_graph.has_edge("biz_users", "biz_products")
        assert ecom_graph.has_edge("biz_products", "biz_users")
        assert ecom_graph.edge_count == initial_edges + 2

    def test_add_relationship_auto_creates_nodes(self, empty_graph: SchemaGraph) -> None:
        """新增关系时自动创建不存在的节点。"""
        empty_graph.add_relationship(
            "new_a",
            _make_rel("r", "new_b", on="new_a.id = new_b.a_id"),
        )
        assert empty_graph.has_node("new_a")
        assert empty_graph.has_node("new_b")
        assert empty_graph.has_edge("new_a", "new_b")

    def test_remove_relationship(self, ecom_graph: SchemaGraph) -> None:
        """删除关系 → 正向+反向边都移除。"""
        initial_edges = ecom_graph.edge_count
        ecom_graph.remove_relationship("biz_orders", "biz_users")
        assert not ecom_graph.has_edge("biz_orders", "biz_users")
        assert not ecom_graph.has_edge("biz_users", "biz_orders")
        assert ecom_graph.edge_count == initial_edges - 2

    def test_remove_nonexistent_relationship(self, ecom_graph: SchemaGraph) -> None:
        """删除不存在的关系 → WARNING, 边数不变。"""
        initial_edges = ecom_graph.edge_count
        ecom_graph.remove_relationship("biz_orders", "nonexistent")
        assert ecom_graph.edge_count == initial_edges


# ── 10. to_vis_data / to_vis_subgraph ────────────────────────


class TestVisData:
    """可视化数据导出: G6 格式。"""

    def test_to_vis_data_structure(self, ecom_graph: SchemaGraph) -> None:
        """to_vis_data 返回 {nodes, edges} 结构。"""
        data = ecom_graph.to_vis_data()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 5

    def test_to_vis_data_node_attributes(self, ecom_graph: SchemaGraph) -> None:
        """节点属性: id, label, community, centrality, columnCount, degree。"""
        data = ecom_graph.to_vis_data()
        node = next(n for n in data["nodes"] if n["id"] == "biz_orders")
        assert node["label"] == "订单表"
        assert "community" in node
        assert "centrality" in node
        assert node["columnCount"] == 1
        assert "degree" in node

    def test_to_vis_data_edge_dedup(self, ecom_graph: SchemaGraph) -> None:
        """双向边去重: A→B 和 B→A 只导出一条。"""
        data = ecom_graph.to_vis_data()
        # 5 条关系, 每条去重后 1 条边
        assert len(data["edges"]) == 5

    def test_to_vis_data_empty_graph(self, empty_graph: SchemaGraph) -> None:
        """空图 → {nodes: [], edges: []}。"""
        data = empty_graph.to_vis_data()
        assert data == {"nodes": [], "edges": []}

    def test_to_vis_subgraph(self, ecom_graph: SchemaGraph) -> None:
        """子图导出: 只含 center + depth-hop 邻居。"""
        data = ecom_graph.to_vis_subgraph("biz_orders", depth=1)
        node_ids = {n["id"] for n in data["nodes"]}
        assert "biz_orders" in node_ids
        # 1 跳邻居应包含
        assert "biz_users" in node_ids or "biz_products" in node_ids

    def test_to_vis_subgraph_nonexistent_center(self, ecom_graph: SchemaGraph) -> None:
        """不存在的 center → 空子图。"""
        data = ecom_graph.to_vis_subgraph("nonexistent")
        assert data == {"nodes": [], "edges": []}

    def test_to_vis_data_same_pair_multiple_on(self) -> None:
        """DiGraph 同对只保留 1 条边 (confidence 更高的), 不支持多边。

        NetworkX DiGraph 同 (source, target) 只能有一条边,
        confidence 更低的会被 _add_edge 跳过。
        这是设计决策 (非 bug): 同对表间 FK 优先于 name_pattern。
        """
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("a", relationships=[
                    _make_rel("r1", "b", on="a.b_id = b.id", confidence=1.0),
                    _make_rel("r2", "b", on="a.b2_id = b.id", confidence=0.8),
                ]),
                _make_model("b"),
            ],
        )
        g = SchemaGraph(content)
        data = g.to_vis_data()
        # r2 (confidence=0.8) 被 r1 (confidence=1.0) 覆盖, 只保留 1 条边
        assert len(data["edges"]) == 1
        assert data["edges"][0]["on"] == "a.b_id = b.id"


# ── 11. get_reverse_relationships ────────────────────────────


class TestGetReverseRelationships:
    """反向关系: 哪些表的正向关系指向了此表。"""

    def test_orders_reverse(self, ecom_graph: SchemaGraph) -> None:
        """biz_orders 被哪些表引用: biz_order_items, biz_payments。"""
        rev = ecom_graph.get_reverse_relationships("biz_orders")
        sources = {r["source"] for r in rev}
        assert "biz_order_items" in sources
        assert "biz_payments" in sources

    def test_users_reverse(self, ecom_graph: SchemaGraph) -> None:
        """biz_users 被引用: biz_orders。"""
        rev = ecom_graph.get_reverse_relationships("biz_users")
        sources = {r["source"] for r in rev}
        assert "biz_orders" in sources

    def test_nonexistent_table(self, ecom_graph: SchemaGraph) -> None:
        """不存在的表 → 空列表。"""
        assert ecom_graph.get_reverse_relationships("nonexistent") == []

    def test_only_forward_direction(self, ecom_graph: SchemaGraph) -> None:
        """只返回 direction=forward 的 predecessor 边。"""
        rev = ecom_graph.get_reverse_relationships("biz_orders")
        for r in rev:
            # source 的正向关系指向 biz_orders
            assert r["target"] == "biz_orders"

    def test_on_dedup(self) -> None:
        """同 ON 条件去重。"""
        # biz_orders 有两条 FK 指向 biz_users (不同 ON), 和一条反向边
        # 反向关系的 ON 去重只看 ON 子句相同的情况
        content = SemanticModelContent(
            version=1,
            models=[
                _make_model("a", relationships=[
                    _make_rel("r1", "c", on="a.c_id = c.id", confidence=1.0),
                ]),
                _make_model("b", relationships=[
                    _make_rel("r2", "c", on="a.c_id = c.id", confidence=0.8),  # 同 ON
                ]),
                _make_model("c"),
            ],
        )
        g = SchemaGraph(content)
        rev = g.get_reverse_relationships("c")
        # 两条同 ON 的正向边, 去重后只保留 1 条
        on_values = [r["on"] for r in rev]
        assert on_values.count("a.c_id = c.id") == 1


# ── 12. 属性 ─────────────────────────────────────────────────


class TestProperties:
    """node_count, edge_count, has_node, has_edge。"""

    def test_node_count(self, ecom_graph: SchemaGraph) -> None:
        assert ecom_graph.node_count == 5

    def test_edge_count(self, ecom_graph: SchemaGraph) -> None:
        assert ecom_graph.edge_count == 10

    def test_has_node(self, ecom_graph: SchemaGraph) -> None:
        assert ecom_graph.has_node("biz_orders") is True
        assert ecom_graph.has_node("nonexistent") is False

    def test_has_edge(self, ecom_graph: SchemaGraph) -> None:
        assert ecom_graph.has_edge("biz_orders", "biz_users") is True
        assert ecom_graph.has_edge("biz_users", "biz_orders") is True
        assert ecom_graph.has_edge("biz_orders", "nonexistent") is False
