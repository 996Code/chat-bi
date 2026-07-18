# Phase E1 上下文：graph-feedback-loop（记忆与图谱集成）

## 需求来源
OpenSpec change：`graph-feedback-loop`
路线图：`doc/chatbi-v2/EVOLUTION-ROADMAP.md` 方向1

## 核心问题
系统有两个独立经验机制各自为政：
- **记忆功能**（recall.py/agent_memory.py）：完整闭环，但只存自然语言经验，不沉淀执行链路
- **图谱 confidence**：扫描期写入后冻结，反哺算法（mine_implicit_relationships）是死代码

本 phase 把两者集成为一：记忆承载链路经验，整理时更新图谱。

## 决策（来自 design.md，已锁定）

### 技术选型
- **存储**：复用记忆基建（markdown 文件 + frontmatter），新建 `memory_type="linkage"`
- **沉淀时机**：查询时直接复用 `AgentState` 现成字段（current_tables/join_path_section/thinking），零额外计算
- **图谱更新时机**：手动整理（`/memory/consolidate`）时触发，轻聚合（查询时已结构化）
- **冲突处理**：乐观锁（version 校验），冲突返回 409 前端弹框（不静默吞错，Fail-Closed）

### 实现方式
1. 链路经验用 `linkage-{tableA}-{tableB}.md` 格式，frontmatter 存 `co_occurrence`/`tables`，content 存 JOIN 路径/场景/SQL
2. `persist_linkage_memory(mem_store, state)`：查询时调用，读 state 字段写入/更新记忆
3. `sync_linkage_to_graph(...)`：整理时调用，读 linkage 算 boost → `apply_confidence_updates` 写新版本
4. `persist_warning` SSE 事件：链路沉淀失败时告知前端（不阻塞主流程）
5. `/memory/consolidate/retry` 端点：版本冲突时前端选"重试"调用

### 不做的事（来自 proposal.md 非目标）
- ❌ 不新建 `relationship_feedback` DB 表（复用记忆文件）
- ❌ 不写定时合并任务（复用整理触发）
- ❌ 不做实时图谱同步（整理时才更新）
- ❌ 不做显式用户纠错按钮
- ❌ 不做 confidence 衰减
- ❌ 不做开关（链路沉淀 + 图谱同步默认开启，仅新表对发现保留 `graph_feedback_discover_new_pairs` 开关）

## 关键代码位置（现状）
- 记忆闭环：`backend/app/ai/recall.py:138`（extract）/ `recall.py:244`（consolidate）/ `recall.py:27`（recall）/ `backend/app/core/agent_memory.py:121`（save）
- 整理 API：`backend/app/api/memory.py:126`（/memory/consolidate）
- 死代码算法：`backend/app/services/knowledge_graph.py:315-420`（mine_implicit + apply_feedback，13 单测）
- 主管线反哺点：`backend/app/api/chat_stream.py:819-842`（_persist 的记忆提炼附近，链路沉淀插入点）
- AgentState 现成字段：`chat_stream.py:365-375`（current_tables/seed_tables/expanded_tables/join_path_section）
- SemanticModel 版本化：`backend/app/db/models.py:135-153`（UniqueConstraint tenant+ds+version）

## 规格参考
- `openspec/changes/graph-feedback-loop/specs/graph-feedback-loop/spec.md`（4 个 Requirement）
- 验收关键：Fail-Closed（失败 SSE 告知 / 冲突弹框，不静默）、零额外计算（只读 state）、查询时结构化（整理轻聚合）
