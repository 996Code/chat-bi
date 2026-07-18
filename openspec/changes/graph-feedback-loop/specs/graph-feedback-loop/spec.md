## ADDED Requirements

### Requirement: 成功查询反哺表共现信号

系统应在每次成功的多表查询后，自动从执行 SQL 中挖掘表共现关系，将信号累积到独立的 feedback 存储，作为图谱 confidence 演化的数据源。

#### Scenario: 多表成功查询累积共现信号
- **WHEN** 用户的一次 TEXT_TO_SQL 查询成功完成，且 `state.current_tables` 包含 ≥ 2 张表
- **THEN** 系统从 `state.sql` 中正则抽取所有 FROM/JOIN 的表名，对每一对已知关系表累加 `relationship_feedback.co_occurrence_count`，并更新 `last_seen_at`
- **AND** 单表查询（无 JOIN 信号）跳过反哺，不产生 feedback 记录

#### Scenario: 反哺失败不阻塞主管线
- **WHEN** 共现信号累积过程抛出任何异常（DB 错误、正则失败等）
- **THEN** 仅记录 WARNING 日志，不影响查询结果返回、不影响其他反哺点（SavedQuery/fewshot/记忆）、不向用户暴露错误

#### Scenario: 总开关可关闭反哺
- **WHEN** 配置 `graph_feedback_enabled = false`
- **THEN** 完全跳过共现信号累积，`_persist` 的反哺块不执行

### Requirement: 已知关系 confidence 批量提升

当某表对的累积共现次数达到阈值时，系统应将其对应 Relationship 的 confidence 提升，让 SchemaGraph 的路径计算更倾向该关系。

#### Scenario: 共现达阈值触发 confidence 提升
- **WHEN** `relationship_feedback` 中某表对的 `co_occurrence_count ≥ graph_feedback_co_occurrence_threshold`（默认 3）且该表对在当前 SemanticModel 的 relationships 中存在
- **THEN** 该 Relationship 的 confidence 提升 `graph_feedback_confidence_boost`（默认 +0.1），封顶 `MAX_CONFIDENCE`（0.95）
- **AND** 合并写入新 SemanticModel 版本，记录 `merged_at`

#### Scenario: 节流避免版本爆炸
- **WHEN** 单次成功查询的共现累积未达任何表对的合并阈值
- **THEN** 不写新 SemanticModel 版本，仅更新 feedback 表的计数
- **AND** 定时任务（每小时）批量合并所有达阈值的表对为单一新版本

#### Scenario: 并发合并冲突保护
- **WHEN** 多个 worker 同时尝试合并同一 SemanticModel
- **THEN** 通过事务级锁（`SELECT FOR UPDATE` 或乐观锁 version 校验）保证只有一个合并成功，其他重试或跳过

### Requirement: 新表对保守发现

当两个表频繁共现但当前 SemanticModel 中没有它们的关系记录时，系统应保守地将该表对识别为潜在新关系，经推断后加入图谱。

#### Scenario: 共现超严格阈值触发新关系推断
- **WHEN** 某表对的 `co_occurrence_count ≥ graph_feedback_new_pair_threshold`（默认 5，比已知关系 boost 阈值更严格）且该表对不在 SemanticModel 的 relationships 中
- **THEN** 调用关系推断（复用 `infer_knowledge_graph` 单对逻辑），若推断成功则加入新 Relationship，初始 confidence 为保守低值（0.5）
- **AND** 新关系标记 `source="implicit_mining"` 以区别于扫描期的 foreign_key/name_pattern/ai_inferred

#### Scenario: 新表对发现可关闭
- **WHEN** 配置 `graph_feedback_discover_new_pairs = false`
- **THEN** 仅提升已知关系 confidence，不发现新表对（保守模式，避免污染）

### Requirement: 反哺过程可观测

反哺的累积、合并、新发现等关键动作应记录日志，便于运维监控图谱演化健康度。

#### Scenario: 关键动作记录日志
- **WHEN** 发生以下任一事件：共现信号累积、达阈值合并、新表对发现、合并失败
- **THEN** 记录 INFO/WARNING 级别结构化日志，含 tenant_id、data_source_id、表对、count、confidence 变化前后值

#### Scenario: 合并失败可追溯
- **WHEN** 合并写回 SemanticModel 失败（锁冲突/校验失败等）
- **THEN** 记录 WARNING 日志含失败原因，feedback 表的待合并记录保留（下次重试），不丢累计数据
