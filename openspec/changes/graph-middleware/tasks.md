<!-- 状态说明: 1-5 节 (实现) + 6.5 (全量回归) 已完成。6.1-6.4 (SchemaGraph/API/schema_utils 单测) 尚未覆盖, 功能代码已落地但缺单测, 待补。 -->

## 1. Backend Graph Service (SchemaGraph)

- [x] 1.1 Create `backend/app/services/graph_service.py` with SchemaGraph class skeleton: `__init__`, `_build_from_content()`, node/edge construction from SemanticModelContent
- [x] 1.2 Implement `expand_tables(seeds, max_depth, max_total)` — shortest path between seeds + community expansion + 1-hop fallback
- [x] 1.3 Implement `find_join_paths(source, target)` — Dijkstra shortest path with weight=1-confidence, return path + ON conditions
- [x] 1.4 Implement `get_join_context(tables)` — find minimal JOIN paths for a set of tables, return structured JoinPath data
- [x] 1.5 Implement `get_communities()` — label_propagation (default) + greedy_modularity (configurable), return community lists
- [x] 1.6 Implement `get_hub_tables(top_k)` — degree centrality, return top-k tables with scores
- [x] 1.7 Implement `get_impact(table)` — all reachable downstream tables
- [x] 1.8 Implement `add_relationship(from_table, rel)` and `remove_relationship(from_table, target)` — bidirectional edge management
- [x] 1.9 Implement `to_vis_data()` and `to_vis_subgraph(center, depth)` — G6-compatible export format (forward edges only for vis)
- [x] 1.10 Add graph config items to `backend/app/core/config.py`: graph_max_join_path_hops, graph_community_algorithm, graph_expand_use_community, graph_join_path_in_prompt

## 2. Backend Graph API

- [x] 2.1 Create `backend/app/api/graph.py` with APIRouter — GET `/graph` (full graph), GET `/graph/subgraph` (subgraph by center+depth)
- [x] 2.2 Add GET `/graph/communities`, GET `/graph/hubs`, GET `/graph/impact` endpoints
- [x] 2.3 Add GET `/graph/join-path` endpoint (source+target params)
- [x] 2.4 Add POST `/graph/relationship` and DELETE `/graph/relationship` endpoints with semantic model version update
- [x] 2.5 Register graph router in `backend/app/api/__init__.py`
- [x] 2.6 Add `graph` API object to `frontend/src/api/index.ts` with TypeScript interfaces (GraphData, GraphNode, GraphEdge, Community, HubTable, JoinPathResult)

## 3. Frontend Graph Visualization

- [x] 3.1 Install @antv/g6: `cd frontend && npm install @antv/g6`
- [x] 3.2 Create `frontend/src/utils/g6-config.ts` — G6 theme config, node/edge style presets, layout config (d3-force), behavior config (drag-canvas, zoom-canvas, drag-node, hover, click-select)
- [x] 3.3 Create `frontend/src/components/SchemaGraph.vue` — G6 container with dynamic import, graph initialization, data binding, resize handling, and destroy lifecycle
- [x] 3.4 Implement force-directed layout rendering with community-based node coloring
- [x] 3.5 Implement edge hover tooltip (ON condition, join_type, source, confidence)
- [x] 3.6 Implement node click-to-focus (highlight 1-hop neighbors, dim others)
- [x] 3.7 Implement minimap plugin
- [x] 3.8 Implement empty state when no graph data
- [x] 3.9 Implement add relationship form (target table, ON condition, join_type, cardinality) with validation
- [x] 3.10 Implement delete relationship (right-click edge → confirm → API call)

## 4. SemanticView Graph Tab Integration

- [x] 4.1 Add "图谱" tab to SemanticView.vue tab panel (alongside existing detail view)
- [x] 4.2 Wire tab switch to load graph data via `graph.full(dataSourceId)` on first activation
- [x] 4.3 Preserve graph state when switching tabs (keep-alive or v-show instead of v-if)
- [x] 4.4 Wire node click in graph to show table detail in existing detail panel (optional cross-link)

## 5. Graph-Driven SQL Generation

- [x] 5.1 Refactor `schema_utils.py` expand_with_relationships() to delegate to SchemaGraph.expand_tables() with BFS fallback on failure
- [x] 5.2 Add `build_join_path_section()` function in schema_utils.py that formats SchemaGraph.get_join_context() result as 【JOIN 路径】 prompt text
- [x] 5.3 Modify `sql_agent.py` to inject 【JOIN 路径】 block after 【schema 与类型约束】 section (controlled by graph_join_path_in_prompt config)
- [x] 5.4 Modify `agent.py` pipeline: construct SchemaGraph, use expand_tables() + get_join_context() instead of direct expand_with_relationships() + build_schema_context()
- [x] 5.5 Modify `chat_stream.py` pipeline: same SchemaGraph integration as agent.py

## 6. Testing

- [ ] 6.1 Unit tests for SchemaGraph: construction, find_join_paths, expand_tables, get_communities, get_hub_tables, get_impact, add/remove relationship, to_vis_data, to_vis_subgraph
- [ ] 6.2 Unit tests for graph API endpoints: all GET/POST/DELETE scenarios including error cases
- [ ] 6.3 Unit tests for schema_utils refactoring: expand_with_relationships delegation, build_join_path_section, BFS fallback
- [ ] 6.4 Integration test: agent pipeline uses SchemaGraph end-to-end (mock semantic model)
- [x] 6.5 Run full test suite: `uv run pytest backend/tests/ -q` — must pass 20+ tests
