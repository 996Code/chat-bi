## 1. 数据模型 + 配置

- [ ] 1.1 新增 `RelationshipFeedback` 模型（`backend/app/db/models.py`）：字段 tenant_id/data_source_id/from_table/to_table/co_occurrence_count/last_seen_at/merged_at，UNIQUE(tenant_id, data_source_id, from_table, to_table)，继承 TenantMixin
- [ ] 1.2 `auto_create_tables` 验证能自动建表（启动时）；补 models 的 `__table_args__` 索引（tenant_id + data_source_id 查询用）
- [ ] 1.3 新增 config 项（`backend/app/core/config.py`，全走环境变量）：
  - `graph_feedback_enabled: bool = True`
  - `graph_feedback_co_occurrence_threshold: int = 3`
  - `graph_feedback_new_pair_threshold: int = 5`
  - `graph_feedback_merge_threshold: int = 10`
  - `graph_feedback_confidence_boost: float = 0.1`
  - `graph_feedback_discover_new_pairs: bool = True`
  - 加进 `_validate_positive_int` 校验白名单（如适用）

## 2. 算法扩展（knowledge_graph.py）

- [ ] 2.1 扩展 `mine_implicit_relationships` 支持新表对发现：共现 ≥ `new_pair_threshold` 且不在 known_pairs 时，调单对推断返回新关系建议（标记 source）
- [ ] 2.2 新增 `apply_confidence_updates(content, updates: dict[tuple[str,str], float])`：遍历 content.relationships 改 confidence，返回新 content（不就地改）
- [ ] 2.3 抽取共现统计为独立函数 `count_co_occurrences(sql: str) -> Counter[tuple[str,str]]`，复用现有 `table_re` 正则（`knowledge_graph.py:351`），供管线调用
- [ ] 2.4 单测：扩展 `test_knowledge_graph_evolution.py` 覆盖新表对发现（阈值边界 / discover_new_pairs=false 跳过 / 新关系 source 标记）

## 3. 主管线接入（chat_stream.py）

- [ ] 3.1 新增 `_persist_graph_feedback(db, user, state, conv_id, data_source_id)` 函数：从 state.sql 抽共现 → 写/更新 relationship_feedback 表（UPSERT 累加 count）
- [ ] 3.2 在 `_persist`（`chat_stream.py:842` 后，记忆提炼之后）插入反哺块：条件 `state.success and state.sql and len(state.current_tables) >= 2 and graph_feedback_enabled`，try/except 失败仅 WARNING 不阻塞
- [ ] 3.3 阈值触发即时合并：累积后检查是否达 `merge_threshold`，达则调合并逻辑（可选，主要靠定时任务）
- [ ] 3.4 单测：`test_chat_stream.py` 补一个反哺接入测试（mock 多表成功查询，断言 feedback 表有记录 + 失败时不阻塞）

## 4. 合并写回 SemanticModel

- [ ] 4.1 新增 `merge_feedback_to_semantic_model(db, tenant_id, data_source_id)`：查待合并 feedback（co_occurrence ≥ threshold 且 merged_at is null）→ 调 mine_implicit_relationships + apply_confidence_updates → 写新 SemanticModel 版本
- [ ] 4.2 并发保护：合并走单事务，`SELECT SemanticModel FOR UPDATE` 或乐观锁 version 校验，冲突时跳过本次（下次重试）
- [ ] 4.3 合并成功后更新 feedback.merged_at；新发现的关系标记 source="implicit_mining"
- [ ] 4.4 单测：合并逻辑测试（已知关系 boost / 新关系加入 / version 递增 / 冲突跳过）

## 5. 定时任务（scheduler.py）

- [ ] 5.1 注册定时任务"图谱反哺批量合并"（每小时），遍历有待合并 feedback 的 (tenant, ds) 调 `merge_feedback_to_semantic_model`
- [ ] 5.2 任务失败不阻塞调度器（复用现有 try/except 模式），记录日志
- [ ] 5.3 config 控制任务间隔（`graph_feedback_merge_interval_minutes: int = 60`）

## 6. 验收测试

- [ ] 6.1 端到端：模拟 3 次同表对成功查询 → feedback 累积 → 触发合并 → 断言 SemanticModel 新版本 confidence 提升
- [ ] 6.2 端到端：模拟 5 次未知表对共现 → 触发新关系发现 → 断言新 Relationship 加入（source=implicit_mining, confidence=0.5）
- [ ] 6.3 关闭开关：`graph_feedback_enabled=false` → 成功查询不产生 feedback 记录
- [ ] 6.4 失败不阻塞：mock feedback 表写入抛异常 → 查询仍正常返回，其他反哺点（fewshot/记忆）仍执行
- [ ] 6.5 全量测试通过：`.venv/bin/python -m pytest backend/tests/ -q`（当前 538 passed 基线不回归）

## 7. 文档

- [ ] 7.1 更新 `doc/chatbi-v2/EVOLUTION-ROADMAP.md`：方向1 状态改为"进行中/已完成"，归档要点写进 ROADMAP.md
- [ ] 7.2 `backend/.env.example` 补 graph_feedback_* 配置项说明
- [ ] 7.3 CHANGELOG.md 新增"图谱反哺闭环"条目
