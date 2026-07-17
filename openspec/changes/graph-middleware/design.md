## Context

ChatBI v2 的 SQL 生成流程依赖 `schema_utils.py` 的 BFS 扩展来补充 JOIN 关联表，LLM 需自行理解行尾 `→target(on)` 提示来决定 JOIN 方式。当前存在三个问题：

1. **BFS 盲目扩展**：超级枢纽表（如 uc_users 有 45 邻居）把无关表拉进 schema_context，浪费 token 并干扰 LLM
2. **LLM 猜 JOIN**：关系信息嵌在 schema_context 行尾，LLM 需自行推断 JOIN 路径和 ON 条件，容易出错
3. **关系不可视**：语义层关系只有扁平表格展示，运维人员无法直观理解表间拓扑

现有基础设施：
- `knowledge_graph.py`：推断关系（name_pattern + ai_inferred），产出 Relationship 建议，**保持不变**
- `semantic_scanner.py`：FK 扫描产出 confidence=1.0 的关系，**保持不变**
- `semantic_layer.py`：Relationship 数据模型（name, target_model, join_type, on, type, source, confidence），**保持不变**
- NetworkX 3.6.1 已随 LangGraph 安装，无需新增后端依赖

## Goals / Non-Goals

**Goals:**
- 用 NetworkX DiGraph 构建语义层图谱，提供最短 JOIN 路径（Dijkstra）、社区发现、中心度分析等图查询能力
- 替代 BFS 扩展为最短路径 + 社区补全的智能扩展，减少无关表进入 schema_context
- 在 SQL prompt 中新增独立【JOIN 路径】块，系统预计算 JOIN 路径和 ON 条件，LLM 直接使用
- SemanticView 新增「图谱」Tab，用 AntV G6 力导向布局渲染 ER 图，支持交互和编辑
- 新增 `/graph` 系列 REST API，返回全图/子图/社区/枢纽/JOIN 路径数据

**Non-Goals:**
- 不替换 `knowledge_graph.py` 的推断逻辑（推断仍由该模块负责，SchemaGraph 只消费其产出）
- 不修改 `semantic_layer.py` 数据模型（Relationship 不加 from_model 字段，from 信息从 Model.name 推断）
- 不做图数据库持久化（SchemaGraph 从 SemanticModelContent 懒构建，无额外存储）
- 不做历史查询挖掘的生产接入（mine_implicit_relationships 仍为算法先行，Phase 3 范畴）
- 不做图驱动的 fewshot 匹配（同社区优先匹配，Phase 3 范畴）

## Decisions

### D1: 图构建策略 — 懒加载从语义层构建，不持久化

**选择**: 每次请求从 SemanticModelContent 构建 SchemaGraph 实例，不持久化到数据库/文件

**替代方案**:
- (A) 图持久化到 Neo4j/ArangoDB：引入新基础设施，运维成本高，语义层变更时需同步
- (B) 图序列化到 Redis/文件：需维护缓存失效策略，语义层版本变更时缓存一致性复杂

**理由**: 语义层本身是 append-only 版本化存储，每次变更产生新版本。SchemaGraph 从最新版本构建即可保证一致性，无需额外缓存层。构建耗时实测 < 50ms（100 表级别），对请求延迟无影响。

### D2: 边权重 = 1 - confidence，Dijkstra 最短路径

**选择**: 有向边权重 `weight = 1 - confidence`，用 Dijkstra 找最短 JOIN 路径

**替代方案**:
- (A) 无权图 BFS：无法区分 FK(1.0) 和 name_pattern(0.6) 路径质量
- (B) 权重 = confidence（越大越好）：需改用最长路径算法，NP-hard 不现实

**理由**: confidence 越高权重越低，Dijkstra 优先走 FK(1.0, weight=0) 路径，其次 ai_inferred(0.7, weight=0.3)，最后 name_pattern(0.6, weight=0.4)。这符合业务直觉：优先使用确定性高的关系。

### D3: 双向边 — JOIN 可从任一方向发起

**选择**: 每个 Relationship 生成两条有向边（正向 + 反向），权重相同

**理由**: SQL JOIN 不区分方向。用户问 biz_products 时需要反向找到 biz_order_items（通过 product_id），正向边只覆盖 order_items→products 方向。双向边确保 Dijkstra 从任一种子表出发都能找到完整路径。

### D4: 社区发现算法 — label_propagation

**选择**: NetworkX label_propagation_communities（非确定性，快速）

**替代方案**:
- (A) Louvain（greedy_modularity_communities）：确定性但 O(n²) 复杂度，100+ 表可接受
- (B) 手动社区定义：需人工维护，违背自动化目标

**理由**: label_propagation O(m) 近线性复杂度，适合实时构建场景。非确定性在语义层不变时结果稳定（同构图同结果）。通过 `graph_community_algorithm` 配置项可切换为 greedy_modularity。

### D5: 前端图库 — AntV G6

**选择**: @antv/g6 v5（力导向布局 d3-force，Vue 3 兼容）

**替代方案**:
- (A) ECharts graph：已有依赖但交互能力弱（无拖拽/聚焦/小地图），不适合 ER 图编辑
- (B) D3.js：底层灵活但开发成本高，需手写所有交互
- (C) Cytoscape.js：学术风格，UI 不符合 Element Plus 设计体系

**理由**: G6 是蚂蚁集团出品，与 Element Plus 同属国内生态，力导向布局开箱即用，支持拖拽/缩放/小地图/Tooltip/边编辑等交互，Vue 3 集成成熟。~500KB gzipped 可接受。

### D6: 图谱 API 路由挂载 — 复用现有 data_source_id 鉴权模式

**选择**: `/graph` 系列端点通过 `data_source_id` 参数获取语义层，复用 data_sources 模块的鉴权逻辑

**理由**: 语义层按 data_source 隔离，图谱数据自然也按 data_source 隔离。无需新增鉴权逻辑，复用现有 `get_current_semantic_model()` 工具函数。

### D7: SQL Prompt JOIN 路径块 — 独立注入，不修改现有 schema_context 格式

**选择**: 在 sql_agent.py 的 prompt 中新增独立 `【JOIN 路径】` 块，保留现有 `【schema 与类型约束】` 格式不变

**替代方案**:
- (A) 修改 schema_context 格式加入 JOIN 路径：破坏现有格式，影响 fewshot 和调试
- (B) 完全替换 schema_context 为图结构化格式：改动过大，风险高

**理由**: 独立块方式增量修改，不影响现有 schema_context 的列信息展示。LLM 同时看到列定义（schema_context）和预计算 JOIN 路径（新块），信息互补不冲突。

## Risks / Trade-offs

- **[图构建性能]** 100+ 表的语义层每次请求构建 SchemaGraph → 缓解：实测 < 50ms，可接受。若后续表数 > 500，可加 LRU 缓存（key=semantic_model_id+version）
- **[社区发现非确定性]** label_propagation 每次结果可能不同 → 缓解：语义层不变时图结构不变，结果稳定。前端可缓存社区结果
- **[G6 包体积]** ~500KB gzipped → 缓解：按需加载（动态 import），只在 SemanticView 图谱 Tab 激活时加载
- **[双向边冗余]** 同一关系存两条边，内存翻倍 → 缓解：100 表级别边数 < 500，内存 < 1MB，可忽略
- **[JOIN 路径可能不存在]** 种子表之间无连通路径 → 缓解：降级为 1-hop 邻居兜底，与现有 BFS 行为一致
- **[向后兼容]** schema_utils.py 接口变更 → 缓解：保留原函数签名，内部委托给 SchemaGraph，调用方无需修改

## Migration Plan

1. **Phase 1（图基础设施 + 可视化）**: 新增 SchemaGraph + graph API + G6 可视化，不影响现有 SQL 生成流程
2. **Phase 2（图驱动 SQL）**: schema_utils.py 内部委托 SchemaGraph，sql_agent.py 新增 JOIN 路径块。两步可独立开关：
   - `graph_expand_use_community=True` 启用社区补全扩展
   - `graph_join_path_in_prompt=True` 启用 JOIN 路径 prompt 块
3. **回滚**: 关闭配置项即可回退到 BFS 行为，无需代码回滚
