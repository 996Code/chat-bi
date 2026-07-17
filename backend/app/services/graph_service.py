"""
SchemaGraph: 基于 NetworkX 的语义层图谱服务

核心能力:
  - 从 SemanticModelContent 构建 NetworkX DiGraph (节点=表, 边=关系)
  - 最短 JOIN 路径 (Dijkstra, weight=1-confidence)
  - 社区发现 (label_propagation / greedy_modularity)
  - 中心度分析 (枢纽表识别)
  - 影响分析 (表依赖)
  - 图修改 (新增/删除关系)
  - 可视化数据导出 (G6 格式)

设计决策 (详见 design.md):
  - D1: 懒加载从语义层构建, 不持久化
  - D2: 边权重 = 1 - confidence, Dijkstra 优先走 FK(1.0) 路径
  - D3: 双向边 — JOIN 可从任一方向发起
  - D4: 社区发现默认 label_propagation (O(m) 近线性)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import networkx as nx

from app.schemas.semantic_layer import Relationship, SemanticModelContent

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class JoinPath:
    """一条 JOIN 路径 (表序列 + ON 条件)。"""
    tables: list[str] = field(default_factory=list)
    on_conditions: list[str] = field(default_factory=list)  # 每跳的 ON 条件
    join_types: list[str] = field(default_factory=list)      # 每跳的 JOIN 类型
    confidences: list[float] = field(default_factory=list)   # 每跳的 confidence
    total_weight: float = 0.0


# ── 边属性键 ──────────────────────────────────────────────────

_EDGE_ON = "on"
_EDGE_JOIN_TYPE = "join_type"
_EDGE_CARDINALITY = "cardinality"
_EDGE_SOURCE = "source"
_EDGE_CONFIDENCE = "confidence"
_EDGE_WEIGHT = "weight"
_EDGE_DIRECTION = "direction"  # "forward" | "reverse"

# 节点属性键
_NODE_DISPLAY_NAME = "display_name"
_NODE_COLUMN_COUNT = "column_count"
_NODE_SOURCE = "source"


class SchemaGraph:
    """基于 NetworkX 的语义层图谱服务。

    用法:
        graph = SchemaGraph(semantic_content)
        paths = graph.find_join_paths("biz_orders", "biz_users")
        expanded = graph.expand_tables(["biz_orders", "biz_users"])
    """

    def __init__(self, content: SemanticModelContent | None = None) -> None:
        self._graph = nx.DiGraph()
        if content is not None:
            self._build_from_content(content)

    # ── 构建 ──────────────────────────────────────────────────

    def _build_from_content(self, content: SemanticModelContent) -> None:
        """从 SemanticModelContent 构建 NetworkX DiGraph。

        节点: 表名 (属性: display_name, column_count, source)
        边: Relationship (正向 + 反向, 属性: on, join_type, cardinality, source, confidence, weight, direction)
        """
        for model in content.models:
            self._graph.add_node(
                model.name,
                **{
                    _NODE_DISPLAY_NAME: model.display_name,
                    _NODE_COLUMN_COUNT: len(model.columns),
                    _NODE_SOURCE: model.source,
                },
            )
            for rel in model.relationships:
                target = rel.target_model
                if not target:
                    continue
                # 正向边: model.name → target
                self._add_edge(model.name, target, rel, direction="forward")
                # 反向边: target → model.name (双向图, JOIN 可从任一方向发起)
                self._add_edge(target, model.name, rel, direction="reverse")

    def _add_edge(
        self, source: str, target: str, rel: Relationship, direction: str = "forward",
    ) -> None:
        """添加一条有向边。

        NetworkX DiGraph 同一对 (source, target) 只能有一条边。
        若已存在同对边, 保留 confidence 更高的那条 (FK 优先于 name_pattern)。

        Args:
            source: 边的起点
            target: 边的终点
            rel: Relationship 对象
            direction: "forward" (正向) 或 "reverse" (反向, 构建时自动添加)
        """
        weight = 1.0 - rel.confidence
        if self._graph.has_edge(source, target):
            existing = self._graph.edges[source, target]
            if rel.confidence <= existing.get(_EDGE_CONFIDENCE, 0.0):
                return  # 已有更高 confidence 的边, 跳过
        self._graph.add_edge(
            source, target,
            **{
                _EDGE_ON: rel.on,
                _EDGE_JOIN_TYPE: rel.join_type,
                _EDGE_CARDINALITY: rel.type,
                _EDGE_SOURCE: rel.source,
                _EDGE_CONFIDENCE: rel.confidence,
                _EDGE_WEIGHT: weight,
                _EDGE_DIRECTION: direction,
            },
        )

    # ── 图查询 ────────────────────────────────────────────────

    def find_join_paths(
        self, source: str, target: str, max_hops: int | None = None,
    ) -> list[JoinPath]:
        """用 Dijkstra 找最短 JOIN 路径 (weight=1-confidence, FK 路径优先)。

        Args:
            source: 起始表名
            target: 目标表名
            max_hops: 最大跳数 (None → 从 config 读 graph_max_join_path_hops)

        Returns:
            JoinPath 列表 (按 total_weight 升序, 通常只有 1 条最短路径)
        """
        if source not in self._graph or target not in self._graph:
            return []
        if source == target:
            return []

        if max_hops is None:
            from app.core.config import get_settings
            max_hops = get_settings().graph_max_join_path_hops

        try:
            # Dijkstra 最短路径 (weight 越小 = confidence 越高 = 优先)
            path_nodes = nx.dijkstra_path(
                self._graph, source, target, weight=_EDGE_WEIGHT,
            )
        except nx.NetworkXNoPath:
            return []

        if len(path_nodes) - 1 > max_hops:
            logger.debug(
                "find_join_paths: %s → %s 路径 %d 跳超过 max_hops=%d, 跳过",
                source, target, len(path_nodes) - 1, max_hops,
            )
            return []

        # 提取每跳的边属性
        on_conditions: list[str] = []
        join_types: list[str] = []
        confidences: list[float] = []
        total_weight = 0.0

        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]
            edge_data = self._graph.edges[u, v]
            on_conditions.append(edge_data[_EDGE_ON])
            join_types.append(edge_data[_EDGE_JOIN_TYPE])
            confidences.append(edge_data[_EDGE_CONFIDENCE])
            total_weight += edge_data[_EDGE_WEIGHT]

        return [JoinPath(
            tables=path_nodes,
            on_conditions=on_conditions,
            join_types=join_types,
            confidences=confidences,
            total_weight=total_weight,
        )]

    def expand_tables(
        self,
        seeds: list[str],
        max_depth: int = 2,
        max_total: int | None = None,
    ) -> list[str]:
        """智能表扩展: 最短路径 + 距离排序邻居 + 社区补全兜底。

        策略 (按精准度排序, 精准的优先):
          1. 种子表之间的最短路径 (补齐 JOIN 中间表) — 最精准
          2. 种子表的邻居 (按图距离排序, 近的优先, 受 max_total 控制)
          3. 社区补全兜底 (按到种子表的图距离排序, 近的优先)
          4. 种子表永远保留, 不受 max_total 影响

        防扩散关键: 不用 BFS 逐层无差别拉入, 而是先收集所有候选,
        按到最近种子表的图距离排序后逐个加入, 距离近的优先占名额。
        这样超级枢纽 (如 uc_users degree=90) 的远亲不会因为遍历顺序
        先占满名额, 把真正需要的近邻挤掉。

        Args:
            seeds: 检索命中的表名
            max_depth: 邻居扩展深度上限 (默认 2 跳, 但按距离排序后近的优先)
            max_total: 扩展后总表数上限 (None → 从 config 读)

        Returns:
            扩展后的表名列表 (含原始命中 + 关联表)
        """
        if not seeds:
            return []

        if max_total is None:
            from app.core.config import get_settings
            max_total = get_settings().rag_max_schema_tables

        # 过滤掉图中不存在的种子表
        valid_seeds = [s for s in seeds if s in self._graph]
        if not valid_seeds:
            return list(seeds)  # 种子不在图中, 原样返回

        result_set = set(seeds)  # 种子表永远保留 (含不在图中的)

        # 阶段 1: 种子表之间的最短路径 (补齐 JOIN 中间表)
        # 这是最精准的扩展: 如果检索命中了多张种子表, 它们之间的
        # JOIN 路径上的桥接表是 SQL 必需的
        for i in range(len(valid_seeds)):
            for j in range(i + 1, len(valid_seeds)):
                if len(result_set) >= max_total:
                    break
                paths = self.find_join_paths(valid_seeds[i], valid_seeds[j])
                if paths:
                    for table in paths[0].tables:
                        if len(result_set) >= max_total:
                            break
                        result_set.add(table)
            if len(result_set) >= max_total:
                break

        # 阶段 2: 邻居扩展 (按图距离排序, 近的优先)
        # 不用 BFS 逐层无差别拉入, 而是先收集 max_depth 跳内所有候选,
        # 按到最近种子表的图距离排序后逐个加入。
        # 这样 dist=1 的直接外键关联表优先占名额, dist=2 的远亲在名额有余时才加入,
        # 超级枢纽的远亲不会因为遍历顺序先占满名额。
        if len(result_set) < max_total:
            neighbor_candidates: list[tuple[int, str]] = []  # (distance, table)
            for seed in valid_seeds:
                for node in self._graph.nodes:
                    if node in result_set or node == seed:
                        continue
                    min_dist = float("inf")
                    for s in valid_seeds:
                        try:
                            dist = nx.shortest_path_length(self._graph, s, node)
                            min_dist = min(min_dist, dist)
                        except nx.NetworkXNoPath:
                            pass
                        try:
                            dist = nx.shortest_path_length(self._graph, node, s)
                            min_dist = min(min_dist, dist)
                        except nx.NetworkXNoPath:
                            pass
                    if 0 < min_dist <= max_depth:
                        neighbor_candidates.append((min_dist, node))

            neighbor_candidates.sort(key=lambda x: x[0])
            for _dist, table in neighbor_candidates:
                if len(result_set) >= max_total:
                    break
                result_set.add(table)
            if neighbor_candidates:
                added = sum(1 for _d, t in neighbor_candidates if t in result_set)
                logger.info(
                    "邻居扩展: 候选 %d 张, 新增 %d 张 (按图距离排序, 最远 %d 跳)",
                    len(neighbor_candidates), added,
                    neighbor_candidates[-1][0] if neighbor_candidates else 0,
                )

        # 阶段 3: 社区补全兜底 (按到种子表的图距离排序, 近的优先)
        # 只在 1-hop 邻居不够时补充同社区远亲, 避免整社区无差别拉入
        from app.core.config import get_settings
        settings = get_settings()

        if settings.graph_expand_use_community and len(result_set) < max_total:
            communities = self.get_communities()
            seed_communities: set[int] = set()
            for idx, community in enumerate(communities):
                if any(s in community for s in valid_seeds):
                    seed_communities.add(idx)

            # 收集社区内尚未加入的表, 按到最近种子表的图距离排序
            community_candidates: list[tuple[int, str]] = []  # (distance, table)
            for idx in seed_communities:
                for table in communities[idx]:
                    if table not in result_set:
                        min_dist = float("inf")
                        for seed in valid_seeds:
                            try:
                                dist = nx.shortest_path_length(self._graph, seed, table)
                                min_dist = min(min_dist, dist)
                            except nx.NetworkXNoPath:
                                pass
                            try:
                                dist = nx.shortest_path_length(self._graph, table, seed)
                                min_dist = min(min_dist, dist)
                            except nx.NetworkXNoPath:
                                pass
                        if min_dist < float("inf"):
                            community_candidates.append((min_dist, table))

            community_candidates.sort(key=lambda x: x[0])
            for _dist, table in community_candidates:
                if len(result_set) >= max_total:
                    break
                result_set.add(table)
            if community_candidates:
                added = sum(1 for _d, t in community_candidates if t in result_set)
                logger.info(
                    "社区补全: 候选 %d 张, 新增 %d 张 (按图距离排序, 最远 %d 跳)",
                    len(community_candidates), added,
                    community_candidates[-1][0] if community_candidates else 0,
                )

        if len(result_set) >= max_total:
            logger.warning(
                "expand_tables: 达到表数上限 %d (种子 %d 张), 截断扩展",
                max_total, len(seeds),
            )

        return list(result_set)

    def get_join_context(self, tables: list[str]) -> list[JoinPath]:
        """给定一组表名, 找到连接它们的最小 JOIN 路径集。

        策略: 用最小生成树思路 — 对所有表对找最短路径, 去重合并。

        Args:
            tables: 需要连接的表名列表

        Returns:
            JoinPath 列表 (去重后的路径集)
        """
        if len(tables) < 2:
            return []

        valid_tables = [t for t in tables if t in self._graph]
        if len(valid_tables) < 2:
            return []

        # 对所有表对找最短路径, 收集去重
        seen_edges: set[tuple[str, str]] = set()
        paths: list[JoinPath] = []

        for i in range(len(valid_tables)):
            for j in range(i + 1, len(valid_tables)):
                found = self.find_join_paths(valid_tables[i], valid_tables[j])
                if not found:
                    # 尝试反向
                    found = self.find_join_paths(valid_tables[j], valid_tables[i])
                if found:
                    path = found[0]
                    # 去重: 检查路径中的边是否已覆盖
                    has_new = False
                    for k in range(len(path.tables) - 1):
                        edge_key = (path.tables[k], path.tables[k + 1])
                        rev_key = (path.tables[k + 1], path.tables[k])
                        if edge_key not in seen_edges and rev_key not in seen_edges:
                            seen_edges.add(edge_key)
                            has_new = True
                    if has_new:
                        paths.append(path)
                else:
                    logger.warning(
                        "get_join_context: %s ↔ %s 无连通路径",
                        valid_tables[i], valid_tables[j],
                    )

        return paths

    # ── 图分析 ────────────────────────────────────────────────

    def get_communities(self) -> list[list[str]]:
        """社区发现: 将表按业务域聚类。

        默认 label_propagation (O(m) 近线性, 非确定性但语义层不变时结果稳定)。
        可通过 graph_community_algorithm 配置切换为 greedy_modularity。

        Returns:
            社区列表, 每个社区是表名列表
        """
        if self._graph.number_of_nodes() == 0:
            return []

        from app.core.config import get_settings
        settings = get_settings()
        algorithm = settings.graph_community_algorithm

        # 校验算法值 (对标 spec: 无效值 → WARNING + 降级 label_propagation)
        valid_algorithms = {"label_propagation", "greedy_modularity"}
        if algorithm not in valid_algorithms:
            logger.warning(
                "get_communities: graph_community_algorithm='%s' 不支持 (可选: %s), 降级为 label_propagation",
                algorithm, ", ".join(sorted(valid_algorithms)),
            )
            algorithm = "label_propagation"

        # 转为无向图做社区发现 (有向图 label_propagation 不稳定)
        undirected = self._graph.to_undirected()

        try:
            if algorithm == "greedy_modularity":
                communities_gen = nx.community.greedy_modularity_communities(undirected)
                return [sorted(list(c)) for c in communities_gen]
            else:
                # 默认 label_propagation
                communities_gen = nx.community.label_propagation_communities(undirected)
                return [sorted(list(c)) for c in communities_gen]
        except Exception as e:
            logger.warning("get_communities: %s 失败, 降级为空: %s", algorithm, e)
            return []

    def get_hub_tables(self, top_k: int = 10) -> list[tuple[str, float]]:
        """枢纽表识别: 度中心度最高的表。

        Args:
            top_k: 返回前 k 个枢纽表

        Returns:
            [(表名, 中心度分数)] 按分数降序
        """
        if self._graph.number_of_nodes() == 0:
            return []

        # 度中心度 (有向图: in + out degree 归一化)
        centrality = nx.degree_centrality(self._graph)
        sorted_hubs = sorted(centrality.items(), key=lambda x: x[1], reverse=True)
        return sorted_hubs[:top_k]

    def get_table_neighbors(self, table: str, depth: int = 1) -> dict:
        """获取表的邻居信息 (可视化用)。

        Args:
            table: 表名
            depth: 邻居深度

        Returns:
            {table: {neighbors: [...], edges: [...]}}
        """
        if table not in self._graph:
            return {}

        neighbors = set()
        for d in range(1, depth + 1):
            for n in nx.descendants_at_distance(self._graph, table, d):
                neighbors.add(n)
            for n in nx.descendants_at_distance(self._graph.reverse(), table, d):
                neighbors.add(n)

        edges = []
        for n in neighbors:
            if self._graph.has_edge(table, n):
                edges.append(self._format_edge(table, n))
            if self._graph.has_edge(n, table):
                edges.append(self._format_edge(n, table))

        return {
            table: {
                "neighbors": sorted(neighbors),
                "edges": edges,
            },
        }

    def get_impact(self, table: str) -> list[str]:
        """影响分析: 从给定表可达的所有下游表。

        Args:
            table: 表名

        Returns:
            受影响的表名列表
        """
        if table not in self._graph:
            return []

        # 有向图: descendants = 从 table 可达的所有节点
        try:
            return sorted(nx.descendants(self._graph, table))
        except Exception:
            return []

    # ── 图修改 ────────────────────────────────────────────────

    def add_relationship(self, from_table: str, rel: Relationship) -> None:
        """新增关系 (正向 + 反向边)。

        Args:
            from_table: 源表名
            rel: Relationship 对象
        """
        target = rel.target_model
        if not target:
            return

        # 确保节点存在
        if from_table not in self._graph:
            self._graph.add_node(from_table, **{_NODE_DISPLAY_NAME: from_table, _NODE_COLUMN_COUNT: 0, _NODE_SOURCE: "manual"})
        if target not in self._graph:
            self._graph.add_node(target, **{_NODE_DISPLAY_NAME: target, _NODE_COLUMN_COUNT: 0, _NODE_SOURCE: "manual"})

        self._add_edge(from_table, target, rel, direction="forward")
        self._add_edge(target, from_table, rel, direction="reverse")

    def remove_relationship(self, from_table: str, target: str) -> None:
        """删除关系 (正向 + 反向边)。

        Args:
            from_table: 源表名
            target: 目标表名
        """
        removed = False
        if self._graph.has_edge(from_table, target):
            self._graph.remove_edge(from_table, target)
            removed = True
        if self._graph.has_edge(target, from_table):
            self._graph.remove_edge(target, from_table)
            removed = True
        if not removed:
            logger.warning(
                "remove_relationship: %s → %s 无关系, 跳过", from_table, target,
            )

    # ── 可视化导出 ────────────────────────────────────────────

    def to_vis_data(self) -> dict:
        """导出全图数据 (G6 渲染格式)。

        只导出正向边 (避免可视化时双向边重叠)。
        节点属性: id, label, community, centrality
        边属性: source, target, on, confidence, source, joinType, cardinality
        """
        if self._graph.number_of_nodes() == 0:
            return {"nodes": [], "edges": []}

        # 社区着色
        communities = self.get_communities()
        community_map: dict[str, int] = {}
        for idx, community in enumerate(communities):
            for table in community:
                community_map[table] = idx

        # 中心度 (用于节点大小)
        centrality = nx.degree_centrality(self._graph)

        nodes = []
        for node, data in self._graph.nodes(data=True):
            nodes.append({
                "id": node,
                "label": data.get(_NODE_DISPLAY_NAME, node),
                "community": community_map.get(node, 0),
                "centrality": round(centrality.get(node, 0.0), 4),
                "columnCount": data.get(_NODE_COLUMN_COUNT, 0),
                "source": data.get(_NODE_SOURCE, "manual"),
                "degree": self._graph.degree(node),
            })

        # 双向导出边: 同一对表 (A→B + B→A) 去重为一对, 但保留两个方向的关系
        # 这样 uc_users (被很多表引用) 能看到所有指向它的边
        # 用 (min, max, on) 去重, 支持同对表间多条关系, 不丢失任一方向
        edges = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for u, v, data in self._graph.edges(data=True):
            on_clause = data.get(_EDGE_ON, "")
            # 用 (排序对, on) 去重: A→B 和 B→A 视为同一条边
            pair_key = tuple(sorted([u, v]))
            key = (*pair_key, on_clause)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            edges.append({
                "source": u,
                "target": v,
                "on": on_clause,
                "confidence": data.get(_EDGE_CONFIDENCE, 0.0),
                "relSource": data.get(_EDGE_SOURCE, "manual"),
                "joinType": data.get(_EDGE_JOIN_TYPE, "LEFT"),
                "cardinality": data.get(_EDGE_CARDINALITY, "N:1"),
            })

        return {"nodes": nodes, "edges": edges}

    def to_vis_subgraph(self, center: str, depth: int = 2) -> dict:
        """导出子图数据 (聚焦某表及其 depth-hop 邻居)。

        Args:
            center: 中心表名
            depth: 邻居深度

        Returns:
            G6 格式子图数据
        """
        if center not in self._graph:
            return {"nodes": [], "edges": []}

        # 收集 depth-hop 内的节点
        subgraph_nodes = {center}
        for d in range(1, depth + 1):
            # 正向邻居
            try:
                for n in nx.descendants_at_distance(self._graph, center, d):
                    subgraph_nodes.add(n)
            except Exception:
                pass
            # 反向邻居
            try:
                for n in nx.descendants_at_distance(self._graph.reverse(), center, d):
                    subgraph_nodes.add(n)
            except Exception:
                pass

        # 提取子图
        subgraph = self._graph.subgraph(subgraph_nodes)

        # 复用 to_vis_data 的逻辑但用子图
        communities = self.get_communities()
        community_map: dict[str, int] = {}
        for idx, community in enumerate(communities):
            for table in community:
                if table in subgraph_nodes:
                    community_map[table] = idx

        centrality = nx.degree_centrality(self._graph)

        nodes = []
        for node in subgraph.nodes():
            data = self._graph.nodes[node]
            nodes.append({
                "id": node,
                "label": data.get(_NODE_DISPLAY_NAME, node),
                "community": community_map.get(node, 0),
                "centrality": round(centrality.get(node, 0.0), 4),
                "columnCount": data.get(_NODE_COLUMN_COUNT, 0),
                "source": data.get(_NODE_SOURCE, "manual"),
                "degree": self._graph.degree(node),
            })

        # 边去重: 与 to_vis_data 统一, 用 (sorted pair, on) 去重
        # 支持同对表间多条关系 (不同 ON 条件), 不丢失任一方向
        edges = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for u, v, data in subgraph.edges(data=True):
            on_clause = data.get(_EDGE_ON, "")
            pair_key = tuple(sorted([u, v]))
            key = (*pair_key, on_clause)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            edges.append({
                "source": u,
                "target": v,
                "on": on_clause,
                "confidence": data.get(_EDGE_CONFIDENCE, 0.0),
                "relSource": data.get(_EDGE_SOURCE, "manual"),
                "joinType": data.get(_EDGE_JOIN_TYPE, "LEFT"),
                "cardinality": data.get(_EDGE_CARDINALITY, "N:1"),
            })

        return {"nodes": nodes, "edges": edges}

    def get_reverse_relationships(self, table: str) -> list[dict]:
        """获取指向该表的反向关系 (其他表引用了此表)。

        语义层中 relationships 是单向定义的 (A→B 在 A.models 里),
        但图谱构建时加了反向边 (B→A, direction="reverse")。
        此方法只返回 direction="forward" 的 predecessor 边,
        即"哪些表的正向关系指向了我"。

        Args:
            table: 被引用的目标表名

        Returns:
            反向关系列表 [{source, target, on, joinType, cardinality, ...}]
        """
        if table not in self._graph:
            return []

        result = []
        seen_on: set[str] = set()
        for predecessor in self._graph.predecessors(table):
            if not self._graph.has_edge(predecessor, table):
                continue
            data = self._graph.edges[predecessor, table]
            # 只看正向边 (direction="forward"): 其他表的正向关系指向了此表
            # 反向边 (direction="reverse") 是构建时自动添加的, 语义上是"我引用了别人"的反向, 不是"别人引用了我"
            if data.get(_EDGE_DIRECTION) != "forward":
                continue
            on_clause = data.get(_EDGE_ON, "")
            if on_clause in seen_on:
                continue
            seen_on.add(on_clause)
            result.append({
                "source": predecessor,
                "target": table,
                "on": on_clause,
                "joinType": data.get(_EDGE_JOIN_TYPE, "LEFT"),
                "cardinality": data.get(_EDGE_CARDINALITY, "N:1"),
                "confidence": data.get(_EDGE_CONFIDENCE, 0.0),
                "relSource": data.get(_EDGE_SOURCE, "manual"),
            })
        return result

    # ── 内部工具 ──────────────────────────────────────────────

    def _format_edge(self, source: str, target: str) -> dict:
        """格式化单条边为可读 dict。"""
        data = self._graph.edges[source, target]
        return {
            "source": source,
            "target": target,
            "on": data.get(_EDGE_ON, ""),
            "confidence": data.get(_EDGE_CONFIDENCE, 0.0),
            "joinType": data.get(_EDGE_JOIN_TYPE, "LEFT"),
            "cardinality": data.get(_EDGE_CARDINALITY, "N:1"),
            "direction": data.get(_EDGE_DIRECTION, "forward"),
        }

    # ── 属性 ──────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def has_node(self, name: str) -> bool:
        return name in self._graph

    def has_edge(self, source: str, target: str) -> bool:
        return self._graph.has_edge(source, target)
