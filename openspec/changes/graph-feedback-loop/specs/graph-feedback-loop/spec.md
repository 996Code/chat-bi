## ADDED Requirements

### Requirement: 查询时结构化沉淀执行链路经验

系统应在每次成功的多表查询过程中，直接复用 `AgentState` 已有的链路数据（不重新计算），将执行链路经验结构化沉淀为 linkage 类记忆，供后续整理和召回使用。

#### Scenario: 多表成功查询沉淀链路经验
- **WHEN** 用户的一次 TEXT_TO_SQL 查询成功完成，且 `state.current_tables` 包含 ≥ 2 张表
- **THEN** 系统直接复用 state 的现成字段（current_tables / seed_tables / expanded_tables / join_path_section / sql / thinking），对每对共现表写入或更新 `type=linkage` 记忆
- **AND** 已存在的表对记忆：`co_occurrence` 计数 +1，必要时追加 JOIN 路径/场景（若新颖）
- **AND** 新表对：创建 linkage 记忆，含表对、JOIN 路径、典型 SQL、co_occurrence=1
- **AND** 单表查询（`len(current_tables) < 2`）跳过，不产生 linkage 记忆

#### Scenario: 链路沉淀零额外计算
- **WHEN** 沉淀链路经验时
- **THEN** 直接使用 state 已有字段（查询过程中图谱扩展/JOIN 路径预计算的产物），不从 SQL 正则重新扫表名、不重算 JOIN 路径

#### Scenario: 链路沉淀失败不阻塞主管线
- **WHEN** 链路记忆写入抛出任何异常
- **THEN** 仅记录 WARNING 日志，不影响查询结果返回、不影响其他反哺点（SavedQuery/fewshot/自然语言记忆）、不向用户暴露错误

#### Scenario: 总开关可关闭链路沉淀
- **WHEN** 配置 `memory_linkage_capture_enabled = false`
- **THEN** 完全跳过链路经验沉淀，不产生 linkage 记忆

### Requirement: 整理时轻聚合更新图谱 confidence

当用户手动触发记忆整理（`/memory/consolidate`）时，系统应在整理完成后，从已结构化的 linkage 记忆里聚合表共现统计，更新 SemanticModel 的 Relationship.confidence，让图谱随使用演化。

#### Scenario: 整理触发图谱 confidence 更新
- **WHEN** `/memory/consolidate` 完成，且配置 `graph_sync_on_consolidate_enabled = true`
- **THEN** 系统遍历该数据源的所有 `type=linkage` 记忆，读 frontmatter 的 `co_occurrence` + `tables`（已结构化，无需解析 SQL）
- **AND** 对 `co_occurrence ≥ graph_linkage_co_occurrence_threshold`（默认 3）的表对，将其 Relationship 的 confidence 提升 `graph_linkage_confidence_boost`（默认 +0.1），封顶 MAX_CONFIDENCE
- **AND** 合并写入新 SemanticModel 版本（单次整理只升一个版本，聚合所有达阈值表对）

#### Scenario: 整理压力轻（查询时已结构化）
- **WHEN** 整理时聚合 linkage 记忆
- **THEN** 仅做"读字段→算 boost→写版本"的轻聚合，不重新解析 SQL、不重算 JOIN 路径（链路在查询时已结构化沉淀）

#### Scenario: 图谱更新失败不影响整理
- **WHEN** 图谱 confidence 更新抛出异常（版本冲突/校验失败等）
- **THEN** 记录 WARNING 日志，但记忆整理本身（consolidate_memories）仍成功完成；图谱更新下次整理再试

#### Scenario: 新表对保守发现
- **WHEN** 某表对 `co_occurrence ≥ new_pair_threshold`（默认 5，比 boost 阈值更严）且该表对不在 SemanticModel 的 relationships 中
- **THEN** 调用关系推断后加入新 Relationship，初始 confidence 0.5，source 标记 `implicit_mining`
- **AND** 配置 `graph_feedback_discover_new_pairs = false` 时跳过新表对发现

### Requirement: 链路经验双路召回

链路经验应同时服务于两条召回路径：自然语言部分注入 prompt 辅助 SQL 生成，结构化部分经图谱 confidence 影响 JOIN 路径计算。

#### Scenario: 链路记忆可被召回注入 prompt
- **WHEN** `recall_memories` 召回时命中 linkage 类记忆
- **THEN** 其 content（典型场景/JOIN 路径）可注入 SQL 生成 prompt，辅助 LLM 理解表间关系

#### Scenario: 链路经验经图谱影响路径计算
- **WHEN** 整理后某表对 confidence 提升
- **THEN** 后续查询构建 SchemaGraph 时（`weight = 1 - confidence`），高 confidence 关系在 Dijkstra 路径计算中被优先选择

### Requirement: 链路经验参与记忆整合

linkage 类记忆应纳入现有 `consolidate_memories` 的整合流程，避免碎片化膨胀。

#### Scenario: 整理合并碎片化 linkage 记忆
- **WHEN** `/memory/consolidate` 整理时
- **THEN** linkage 类记忆也参与 LLM 合并去重（复用现有 consolidate_memories 逻辑）
- **AND** 低 co_occurrence 的 linkage 记忆可被合并/标记 consolidated 隐藏
- **AND** MEMORY.md 索引的 200 行/25KB 截断保护覆盖 linkage 记忆
