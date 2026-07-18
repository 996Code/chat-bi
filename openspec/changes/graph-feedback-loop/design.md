## Context

系统有两个独立经验机制：
- **记忆**（`recall.py` / `agent_memory.py` / `api/memory.py`）：markdown 文件存储，有完整闭环。`extract_memory_from_turn`（`recall.py:138`）从查询提炼自然语言经验；`consolidate_memories`（`recall.py:244`）LLM 合并去重碎片；`/memory/consolidate` API（`memory.py:126`）手动触发整理；`recall_memories`（`recall.py:27`）关键词召回注入 prompt。记忆有 `memory_type`（user/feedback/project/reference）和 `consolidated` 标记。
- **图谱 confidence**：扫描期写入后冻结。`mine_implicit_relationships`（`knowledge_graph.py:315`）+ `apply_feedback_signals`（`knowledge_graph.py:373`）算法就绪但是死代码。

**本设计的核心洞察**：两者都是"成功查询→经验沉淀→整合→影响下次生成"，重复造了存储+整合轮子。集成后，记忆承载完整经验（含链路），整理时汇总更新图谱——一套基建，两个出口（prompt + 图谱路径）。

## Goals / Non-Goals

**Goals:**
- 记忆承载执行链路经验（表共现/JOIN 路径/SQL 模式），不止自然语言
- 手动整理（`/memory/consolidate`）时，自动汇总链路经验更新图谱 confidence
- 复用已有记忆基建（存储/整合/召回），不新建表、不写定时任务
- 图谱 confidence 能随使用演化（受整理频率控制，不爆炸）

**Non-Goals:**
- 实时图谱同步（整理时才更新，最终一致）
- confidence 衰减
- 自动定时整理（保持手动/半手动触发）
- 显式纠错按钮

## Decisions

### D1: 链路经验作为独立 memory_type="linkage" 存储

**选择**：扩展记忆，新增 `memory_type="linkage"`，专门存执行链路经验。与自然语言经验（type=project/feedback）分开，便于整理时定向汇总。

**链路记忆文件格式**（markdown + frontmatter，文件名仍是 UUID 遵守现有约定）：
```markdown
---
id: {uuid}                        ← 遵守现有约定 (filename = immutable id)
name: 表共现 biz_orders↔biz_users  ← 可编辑标题
description: 表共现经验
metadata:
  type: linkage
  consolidated: false
  co_occurrence: 12               ← 结构化字段, 整理时读
  tables: [biz_orders, biz_users] ← 表对 (字典序), 定位用
---

## 典型场景
订单 + 用户信息关联查询

## JOIN 路径
biz_orders.user_id = biz_users.id

## 典型 SQL 模式
SELECT ... FROM biz_orders JOIN biz_users ON ...
```

**按表对查询（`get_linkage_memory`）**：文件名是 UUID 不含表对信息，通过遍历 `type=linkage` 记忆、匹配 `metadata.tables` 定位。linkage 记忆数量有限（受表对组合数限制），遍历可接受。

**为什么用 markdown 不用 JSON**：人可读、可编辑修正（运维能直接改）、和现有记忆格式一致、能被 `recall_memories` 召回注入 prompt。

**备选（linkage 破例用确定性文件名 `linkage-{tableA}-{tableB}.md`）**：❌ 破坏"filename=immutable UUID"的统一约定（`agent_memory.py:149`），与其他记忆类型不一致。

**备选（JSON 结构化）**：❌ 与现有记忆格式不一致，召回逻辑要分叉；人不可读。

### D2: 链路经验在查询时就结构化沉淀（复用 state 现成数据，零额外计算）

**核心优化**：查询过程中 `AgentState` **已经算好了全套链路数据**（图谱扩展/JOIN 路径是管线固定步骤），直接复用，不查询后再去 SQL 里正则扫表名（避免重复计算）。

**可复用的 state 字段**（查询时必然产生，`chat_stream.py`）：
- `state.current_tables`（用到的所有表，含扩展后，line 365）
- `state.seed_tables`（检索命中的种子表，line 366）
- `state.expanded_tables`（图谱扩展新增的表，line 367）
- `state.join_path_section`（**预计算的 JOIN 路径**，line 375）
- `state.sql`（最终 SQL）
- `state.thinking`（预思考：聚合方式/陷阱）

**沉淀时机**：在 `_persist`（`chat_stream.py:819` 现有记忆提炼附近）追加一步，对每对共现表（从 `current_tables` 两两组合，或直接用 `join_path_section` 解析出的表对）：
- 若该表对的 linkage 记忆已存在 → `co_occurrence += 1`，必要时追加场景/SQL（若新颖）
- 若不存在 → 新建 linkage 记忆，`co_occurrence = 1`，写入 JOIN 路径 + 场景

**记忆定位**：文件名用 UUID（遵守现有约定），通过 `metadata.tables`（表名字典序）唯一标识表对；`get_linkage_memory` 遍历 linkage 记忆按 metadata 匹配。

**复用点**：走 `AgentMemoryStore.save_memory(mem_id=existing)` 更新（`agent_memory.py:121` 已支持 mem_id 更新）。

**为什么查询时就沉淀而非暂存**：
- state 数据现成，写入是纯数据搬运，几乎零成本
- 整理时（D3）只需聚合已结构化的 co_occurrence，从"重计算"降级为"轻聚合"
- 失败不阻塞主流程，但通过新增 SSE 事件（`persist_warning`）告知前端（Fail-Closed：不静默吞错）

**节流保护**：单表查询（`len(current_tables) < 2`）跳过。链路沉淀默认开启，无开关。

### D3: 整理时只做轻聚合 + 更新图谱（查询时已结构化，整理压力小）

**选择**：现有 `/memory/consolidate`（`memory.py:126`）整理完后，追加 `sync_linkage_to_graph(tenant_id, ds_id)`：
1. 遍历该 ds 下所有 `type=linkage` 记忆
2. 读 frontmatter 的 `co_occurrence` + `tables`（**已结构化，无需解析 SQL**）
3. 按 `co_occurrence ≥ threshold` 筛选，算 boost 值
4. 调新增 `apply_confidence_updates(content, updates)` → 写新 SemanticModel 版本

**为什么整理压力小**：因为 D2 在查询时就把链路结构化好了（表对/JOIN/co_occurrence 都现成），整理只做"读字段→算 boost→写版本"，不再扫 SQL、不再算 JOIN 路径。

**为什么在整理时做而非每次查询**：
- 避免版本爆炸（整理是低频手动动作）
- 整理本身就是"经验沉淀到结构化"的语义时机

**版本冲突处理（一致性优先，Fail-Closed）**：
- 整理与图谱同步是**原子语义**，不静默跳过失败（违反项目 Fail-Closed 法则）
- 图谱更新用乐观锁：读 SemanticModel 时记 `expected_version`，写时 `WHERE version = expected_version`
- 冲突时（version 不匹配）→ 整理 API 返回 `409 Conflict` + 详情（current_version、expected_version、待更新的表对列表）
- 前端弹框让用户选：①重试（基于最新版本重算 boost）②仅保留记忆整理（放弃图谱更新）③取消（回滚记忆整理若未提交）
- 新增 `POST /memory/consolidate/retry` 端点支持重试（带最新 version）
- 图谱同步默认开启，无开关

### D4: mine_implicit_relationships 改造——从记忆读取

**选择**：原算法入参是 `(query_history: list[str], existing_relationships)`。改造为也能接受 `(linkage_memories: list[dict], existing_relationships)`——从记忆的 co_occurrence 字段读共现次数，而非正则扫 SQL。

**保留原入参签名**：向后兼容已有单测（13 个）。新增一个适配函数 `linkage_memories_to_cooccurrence()` 转换。

### D5: 配置项（进 config.py）

- `graph_linkage_co_occurrence_threshold: int = 3`（boost 阈值，复用 FREQUENT_JOIN_THRESHOLD 语义）
- `graph_linkage_new_pair_threshold: int = 5`（新表对发现阈值，更保守）
- `graph_linkage_confidence_boost: float = 0.1`
- `graph_feedback_discover_new_pairs: bool = True`（新表对发现总开关，保守起见保留）

> 注：链路沉淀（memory_linkage_capture）和图谱同步（graph_sync_on_consolidate）是默认行为，**不做开关**。失败按 Fail-Closed 处理（`persist_warning` SSE 告知**所有反哺失败** / 冲突弹框），不通过开关回避。

## Risks / Trade-offs

- **[linkage 记忆膨胀]** → 整理时 `consolidate_memories` 合并低频表对；co_occurrence 低的可标记 consolidated 隐藏；MEMORY.md 索引有 200 行/25KB 截断保护（`agent_memory.py`）
- **[从 markdown 解析 co_occurrence 有成本]** → 格式约定固定（frontmatter 字段），解析简单；且整理时才解析（低频），非每次查询
- **[_persist 反哺失败]** → **所有反哺点**（SavedQuery/fewshot/记忆提炼/链路沉淀）失败都不静默吞错，统一发 `persist_warning` SSE 事件告知前端（含 stage/error/question），complete 照常 success=true 不阻塞主流程；前端非阻塞 toast
- **[整理时图谱版本冲突]** → 一致性优先，不静默跳过；返回 409 + 详情，前端弹框让用户处理（重试/仅保留记忆/取消）；提供 retry 端点
- **[persist_warning 淹没用户]** → toast 非阻塞可叠加；多个失败合并带计数展示；文案含 question 片段定位是哪轮查询；不弹模态不强制操作
- **[linkage 记忆和自然语言记忆召回干扰]** → `recall_memories` 可按 type 过滤；或召回时 linkage 的 content 也注入（表共现信息对 SQL 生成有用）
- **[错误查询污染 linkage]** → 多次共现才 boost（统计过滤）；人可编辑 linkage 文件修正；未来方向5/P0-7 补评估

## Migration Plan

1. 新增 `memory_type="linkage"` 支持（agent_memory 已支持任意 type 字符串，无需 schema 迁移）
2. 扩展 `extract_memory_from_turn` 抽取链路
3. `/memory/consolidate` 追加图谱同步步骤
4. 新增 config 项（带默认值）
5. **回滚**：链路沉淀和图谱同步是默认行为，无开关；如需紧急停止，注释代码或通过 `graph_feedback_discover_new_pairs=false` 关闭新表对发现；已有 linkage 记忆可保留不影响
6. 无数据迁移（现有记忆/图谱不动）

## Open Questions

> 以下问题已在 spec.md 中作出倾向性决定，执行时按 spec 走；如遇新证据可重新评估。

- ~~linkage 记忆召回时，是注入 prompt 还是只用于图谱？~~ **已在 spec 决定（双路召回 Requirement）**：两者都——content 注入 prompt 辅助 SQL 生成，co_occurrence 经图谱影响路径计算。
- ~~新表对发现是否在整理时一并推断加入？初始 confidence？~~ **已在 spec 决定（新表对保守发现 Scenario）**：是，整理时发现，保守阈值 5，初始 confidence 0.5，source="implicit_mining"，可通过 `graph_feedback_discover_new_pairs` 关闭。
