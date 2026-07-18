## Context

知识图谱的关系 confidence 是 SchemaGraph 路径计算的核心权重（`graph_service.py:113` `weight = 1 - confidence`，Dijkstra 走高 confidence 路径）。当前 confidence 来源只有扫描期三处（外键/name_pattern/LLM 推断），写完即冻住。

反哺算法 `mine_implicit_relationships`（`knowledge_graph.py:315-370`）已实现：正则抽 SQL 表名 → 统计已知关系表对共现 → 共现 ≥3 次（`FREQUENT_JOIN_THRESHOLD=50`）每次 boost +0.1 封顶 0.95。有 5 个单测覆盖。但零生产调用方。

主管线 `_persist`（`chat_stream.py:686-845`）在 `if state.success and state.sql:` 块里已有三个并列反哺点（SavedQuery line 774 / fewshot line 806 / 记忆提炼 line 819），全部 try/except 失败不阻塞。本反哺应同模式插入。

**关键约束**：SemanticModel 是 append-only 版本化（`models.py:135-153` UniqueConstraint tenant+ds+version），每次编辑 confidence 都升 version。1000 次成功查询 = 1000 个版本，不可接受。

## Goals / Non-Goals

**Goals:**
- 成功查询的表共现信号能反哺到 Relationship.confidence
- 不导致 SemanticModel 版本爆炸
- 失败不阻塞主管线（复用现有 try/except 模式）
- 复用已实现 + 已测试的 `mine_implicit_relationships` 算法

**Non-Goals:**
- 不做显式用户纠错（详见 proposal 非目标）
- 不做 confidence 衰减
- 不改 SchemaGraph 运行时构建逻辑
- 不做实时性保证（反哺是最终一致，节流批量合并）

## Decisions

### D1: 节流策略——独立 feedback 表累计 + 阈值触发批量合并

**选择**：新增 `RelationshipFeedback` 表累计共现计数，达阈值（如单表对累计 10 次）或定时任务（每小时）批量合并回 SemanticModel 升一个版本。

**备选 A（每次查询直接升版本）**：❌ 版本爆炸，1000 查询 = 1000 版本。
**备选 B（内存累积，进程重启丢失）**：❌ 多 worker 不共享，信号不可靠。
**备选 C（Redis 累积）**：可，但引入 Redis 依赖做持久化；DB 表更简单且可审计。

**feedback 表结构**：
```
relationship_feedback
- tenant_id, data_source_id
- from_table, to_table      -- 表对
- co_occurrence_count       -- 共现次数 (累加)
- last_seen_at              -- 最近一次共现时间
- merged_at                 -- 最近一次合并回 SemanticModel 的时间 (null=待合并)
UNIQUE(tenant_id, data_source_id, from_table, to_table)
```

### D2: 新表对发现——保守策略，共现超阈值才推断

**选择**：扩展 `mine_implicit_relationships`，当未知表对共现 ≥ `NEW_PAIR_DISCOVERY_THRESHOLD`（默认 5，比已知关系 boost 阈值 3 更严格）时，调 `infer_knowledge_graph` 单对推断后返回。

**备选（共现就加）**：❌ 一次偶然的错查询就永久污染关系。保守阈值降低噪声。
**风险**：阈值高了发现慢，低了易污染——做成 config 可调。

### D3: 合并写回——复用 `_apply_inferred_relationships` 模式但只改数值

**选择**：新增 `apply_confidence_updates(content, updates: dict[tuple[str,str], float])`，遍历 content.relationships，对在 updates 里的表对 `rel.confidence = new_val`，整体升一个 version 写回。

**不复用 `_apply_inferred_relationships`**：那个是追加新 Relationship，本场景是改已存在的 confidence 数值，语义不同。

### D4: 触发点——_persist 内，单表查询跳过

**选择**：插入 `chat_stream.py:842` 后（现有反哺之后），条件 `state.success and state.sql and len(state.current_tables) >= 2`。单表查询无 JOIN 信号，跳过省开销。

### D5: 配置项（进 config.py，走环境变量）

- `graph_feedback_enabled`（默认 true，总开关）
- `graph_feedback_co_occurrence_threshold`（已知关系 boost 阈值，复用 `FREQUENT_JOIN_THRESHOLD`，默认 3）
- `graph_feedback_new_pair_threshold`（新表对发现阈值，默认 5）
- `graph_feedback_merge_threshold`（触发合并的累计次数，默认 10）
- `graph_feedback_confidence_boost`（每次 boost 幅度，默认 0.1）

## Risks / Trade-offs

- **[版本数仍会增长]** → 阈值合并 + 定时任务；监控版本增长率，异常告警
- **[错误查询污染（能跑通但表选错）]** → 共现多次才 boost（统计意义过滤偶发错误）；confidence 只升不崩，最坏是某关系略偏高，不会致命；未来方向5/P0-7 补评估和纠错
- **[新表对推断错误]** → 保守阈值 5 + confidence 初始值低（0.5）；可配置关闭 `graph_feedback_discover_new_pairs`
- **[多 worker 并发合并同一 SemanticModel]** → 合并走单事务 `SELECT ... FOR UPDATE` 或乐观锁（version 校验），失败重试
- **[feedback 表膨胀]** → merged_at + 定期清理已合并且旧的记录

## Migration Plan

1. 新增 `RelationshipFeedback` 表（`auto_create_tables` 会自动建）
2. 新增 config 项（带默认值，无需改 .env）
3. 部署后自动生效（首次成功查询即开始累计）
4. **回滚**：设 `graph_feedback_enabled=false` 即停；feedback 表可保留不删（不影响查询）
5. 无数据迁移（现有 SemanticModel 不动，只增量更新）

## Open Questions

- 定时合并任务用 APScheduler（已有调度器 `scheduler.py`）还是只在阈值触发时同步合并？倾向 APScheduler 每小时跑，阈值触发只标记"待合并"。
- confidence 初始值：新表对发现后初始 confidence 设多少？0.5（保守，需多次才到可用区间）还是 0.6（对齐 name_pattern）？倾向 0.5。
