## Why

当前 SQL 生成依赖 BFS 盲目扩展关联表（2 跳、max_total 截断），LLM 需要自行理解 `→target(on)` 行尾提示来决定 JOIN 方式——这导致：(1) 超级枢纽表（如 uc_users 有 45 邻居）把无关表拉进 schema_context，浪费 token 并干扰 LLM；(2) LLM 猜错 JOIN 路径，生成冗余或错误的 JOIN 子句；(3) 语义层的关系数据只有扁平表格展示，运维人员无法直观理解表间拓扑。引入 NetworkX 图中间件可同时解决查询优化（最短路径替代 BFS、社区发现限制扩展范围）和可视化（ER 图谱）两个核心问题。

## What Changes

- **新增 `SchemaGraph` 服务类**：基于 NetworkX 的有向图，从语义层 SemanticModelContent 构建，提供最短 JOIN 路径（Dijkstra，权重=1-confidence）、社区发现（label_propagation）、中心度分析、影响分析等图查询能力
- **新增图谱 API 端点**：`/graph` 系列 REST API，返回全图/子图/社区/枢纽/JOIN 路径数据，支持关系的新增和删除
- **新增前端图谱可视化**：SemanticView 增加「图谱」Tab，使用 AntV G6 力导向布局渲染 ER 图，支持悬停/聚焦/拖拽/缩放/小地图，按社区着色
- **替代现有 BFS 扩展**：`schema_utils.py` 的 `expand_with_relationships()` 改用 `SchemaGraph.expand_tables()`，基于最短路径 + 社区补全替代纯 BFS
- **SQL Prompt 重构**：在 `sql_agent.py` 中新增独立 `【JOIN 路径】` prompt 块，系统预计算 JOIN 路径和 ON 条件，LLM 直接使用不再猜测
- **新增配置项**：`graph_max_join_path_hops`、`graph_community_algorithm`、`graph_expand_use_community` 等

## Capabilities

### New Capabilities
- `schema-graph`: NetworkX 图谱服务核心——图构建、最短路径、社区发现、中心度分析、影响分析、图修改、可视化数据导出
- `graph-visualization`: 前端 ER 图谱可视化——AntV G6 力导向布局、交互行为（悬停/聚焦/拖拽/缩放/小地图）、社区着色、关系编辑
- `graph-sql-driving`: 图驱动的 SQL 生成——替代 BFS 的智能表扩展、预计算 JOIN 路径 prompt 块、减少 LLM JOIN 推理负担

### Modified Capabilities

## Impact

- **后端新增文件**: `app/services/graph_service.py`（SchemaGraph 类）、`app/api/graph.py`（图谱 API 路由）
- **后端修改文件**: `app/ai/schema_utils.py`（BFS → SchemaGraph）、`app/ai/agent.py`（调用路径变更）、`app/api/chat_stream.py`（调用路径变更）、`app/ai/sql_agent.py`（新增 JOIN 路径 prompt 块）、`app/core/config.py`（新增图配置项）、`app/api/__init__.py`（注册 graph 路由）
- **前端新增文件**: `src/components/SchemaGraph.vue`（G6 图谱组件）、`src/utils/g6-config.ts`（G6 配置）
- **前端修改文件**: `src/views/SemanticView.vue`（加图谱 Tab）、`src/api/index.ts`（新增 graph API）、`package.json`（新增 @antv/g6 依赖）
- **已有依赖**: NetworkX 3.6.1 已随 LangGraph 安装，无需新增后端依赖
- **新增前端依赖**: @antv/g6（~500KB gzipped）
- **不修改**: `knowledge_graph.py`（推断逻辑保持不变）、`semantic_scanner.py`（FK 扫描不变）、`semantic_layer.py`（数据模型不变）
