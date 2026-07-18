## 1. 记忆基建扩展（支持 linkage 类型）

- [ ] 1.1 确认 `AgentMemoryStore.save_memory` 已支持任意 `memory_type` 字符串（含 "linkage"），无需 schema 迁移；验证 frontmatter 的 metadata 字段能存 co_occurrence/tables
- [ ] 1.2 新增 linkage 记忆文件名约定：`linkage-{tableA}-{tableB}.md`（表名字典序保证对唯一），在 `_get_ds_store` / `_validate_memory_name` 确认能正确处理
- [ ] 1.3 新增 helper `get_linkage_memory(mem_store, table_a, table_b) -> dict | None`：按表对查现有 linkage 记忆（文件名约定定位）
- [ ] 1.4 单测：linkage 记忆的创建/更新/frontmatter 读写（`test_agent_memory.py` 或新建 `test_recall.py`）

## 2. 链路经验查询时结构化沉淀（复用 state，零额外计算）

- [ ] 2.1 新增 `persist_linkage_memory(mem_store, state)` 函数（在 `recall.py` 或新模块）：直接用 `state.current_tables` 两两组合（或从 `state.join_path_section` 解析表对），不重新扫 SQL
- [ ] 2.2 已存在的表对：`co_occurrence += 1`，若 JOIN 路径/场景新颖则追加；走 `save_memory(mem_id=existing)` 更新
- [ ] 2.3 新表对：创建 linkage 记忆，写入表对 + JOIN 路径（from join_path_section）+ 典型 SQL（截断）+ 场景（from thinking）+ co_occurrence=1
- [ ] 2.4 在 `_persist`（`chat_stream.py:819` 记忆提炼附近）插入链路沉淀块：条件 `state.success and state.sql and len(state.current_tables) >= 2`，**失败时发 `persist_warning` SSE 事件告知前端**（不静默吞错，Fail-Closed）
- [ ] 2.5 单测：多表成功查询 → linkage 记忆正确创建/更新；单表查询跳过；失败发 persist_warning 事件不阻塞主流程

## 3. SSE 持久化警告事件（所有反哺失败的前端告知通道）

- [ ] 3.1 后端新增 SSE 事件类型 `persist_warning`（`chat_stream.py` emit 机制）：携带 `{stage, error, conversation_id, question}`
  - `stage` 枚举：`saved_query` / `fewshot` / `memory_extract` / `linkage`
  - `error` 脱敏（对标 complete 的 error 处理，不泄露内部细节）
- [ ] 3.2 `persist_warning` 在 complete 事件之前发送，complete 照常发（success 仍 true，查询本身成功）；多个反哺失败可发多个 persist_warning
- [ ] 3.3 **改造 `_persist` 所有反哺点**（SavedQuery line 774 / fewshot line 806 / 记忆提炼 line 819 / 链路沉淀）的 try/except：从静默 WARNING 改为发 persist_warning 事件（Fail-Closed，统一告知）
- [ ] 3.4 前端 `ChatView.vue` handleSSEEvent 新增 `case 'persist_warning'`：非阻塞 ElMessage warning toast，文案含 question 片段（如"查询『本月销售』的后台保存失败，不影响结果"）；多个可叠加或合并带计数
- [ ] 3.5 单测：mock 各反哺点抛异常 → SSE 流含对应 stage 的 persist_warning + complete 正常 success=true

## 4. 配置项（config.py，走环境变量）

- [ ] 4.1 新增 config 项（无开关，仅阈值/参数）：
  - `graph_linkage_co_occurrence_threshold: int = 3`
  - `graph_linkage_new_pair_threshold: int = 5`
  - `graph_linkage_confidence_boost: float = 0.1`
  - `graph_feedback_discover_new_pairs: bool = True`（仅新表对发现开关）
- [ ] 4.2 加入 `_validate_positive_int` 校验白名单；补 `.env.example` 说明

## 5. 整理时轻聚合 + 图谱更新（一致性优先，Fail-Closed）

- [ ] 5.1 新增 `apply_confidence_updates(content, updates, expected_version)`（`knowledge_graph.py`）：乐观锁，WHERE version = expected_version，冲突抛 `VersionConflictError`
- [ ] 5.2 新增 `linkage_memories_to_cooccurrence(mem_store) -> dict[tuple[str,str], int]`：从 linkage 记忆 frontmatter 读 co_occurrence（轻聚合）
- [ ] 5.3 新增 `sync_linkage_to_graph(db, tenant_id, data_source_id, expected_version)`：读 linkage → 筛达阈值表对 → 算 boost（+新表对发现）→ `apply_confidence_updates` → 写新版本；**冲突时抛异常不吞错**
- [ ] 5.4 新表对发现：共现 ≥ `new_pair_threshold` 且不在 relationships 时，调单对推断加入，初始 confidence 0.5，source="implicit_mining"
- [ ] 5.5 在 `/memory/consolidate`（`memory.py`）整理后调 `sync_linkage_to_graph`；**冲突时返回 409 + 详情**（current_version、expected_version、待更新表对列表）
- [ ] 5.6 新增 `POST /memory/consolidate/retry` 端点：基于最新 version 重新算 boost 并合并（用户选"重试"时调）
- [ ] 5.7 单测：已知关系 boost / 新关系加入 / version 递增 / **冲突抛 VersionConflictError 不静默**

## 6. 前端冲突处理弹框

- [ ] 6.1 `/memory/consolidate` API 返回 409 时，前端捕获 conflict 详情
- [ ] 6.2 弹框（ElMessageBox）展示冲突信息，三个选项：①重试（调 `/memory/consolidate/retry`）②仅保留记忆整理（放弃图谱更新，确认当前整理结果）③取消（回滚）
- [ ] 6.3 重试成功后正常关闭；重试仍冲突则刷新版本号再次提示
- [ ] 6.4 单测/手测：模拟并发整理触发 409 → 弹框 → 各选项行为正确

## 7. 链路经验参与记忆整合 + 双路召回

- [ ] 7.1 确认 `consolidate_memories` 能处理 linkage 类记忆（LLM 整理时合并低频表对）；验证 MEMORY.md 索引截断保护覆盖 linkage
- [ ] 7.2 确认 `recall_memories` 召回时能命中 linkage 记忆（content 注入 prompt 辅助 SQL 生成）；必要时调整召回逻辑不过滤 linkage 类型
- [ ] 7.3 单测：整理合并碎片化 linkage；召回命中 linkage 注入 prompt

## 8. 验收测试

- [ ] 8.1 端到端：3 次同表对成功查询 → linkage 记忆 co_occurrence=3 → 整理触发 → SemanticModel 新版本 confidence 提升
- [ ] 8.2 端到端：5 次未知表对共现 → 整理触发新关系发现 → 新 Relationship 加入（source=implicit_mining, confidence=0.5）
- [ ] 8.3 反哺失败告知：mock 各反哺点（SavedQuery/fewshot/记忆/链路）抛异常 → SSE 流含对应 stage 的 persist_warning + complete 正常 success=true + 前端 toast 含 question 片段
- [ ] 8.4 图谱冲突：并发整理 → 第二个返回 409 + 详情 → 前端弹框 → 重试成功
- [ ] 8.5 零额外计算验证：mock 查询，断言沉淀过程只读 state 字段，不调用 SQL 正则/JOIN 重算
- [ ] 8.6 全量测试通过：`.venv/bin/python -m pytest backend/tests/ -q`（538 passed 基线不回归）

## 9. 文档

- [ ] 9.1 更新 `doc/chatbi-v2/EVOLUTION-ROADMAP.md`：方向1 状态改为"已完成"，记录最终设计决策（含 Fail-Closed/SSE 告知/冲突弹框）
- [ ] 9.2 `backend/.env.example` 补 graph_linkage_* / graph_feedback_discover_new_pairs 配置项（无开关项）
- [ ] 9.3 CHANGELOG.md 新增"记忆与图谱集成闭环"条目
