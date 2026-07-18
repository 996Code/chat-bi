## 1. 记忆基建扩展（支持 linkage 类型）

- [ ] 1.1 确认 `AgentMemoryStore.save_memory` 已支持任意 `memory_type` 字符串（含 "linkage"），无需 schema 迁移；验证 frontmatter 的 metadata 字段能存 co_occurrence/tables
- [ ] 1.2 新增 linkage 记忆文件名约定：`linkage-{tableA}-{tableB}.md`（表名字典序保证对唯一），在 `_get_ds_store` / `_validate_memory_name` 确认能正确处理
- [ ] 1.3 新增 helper `get_linkage_memory(mem_store, table_a, table_b) -> dict | None`：按表对查现有 linkage 记忆（文件名约定定位）
- [ ] 1.4 单测：linkage 记忆的创建/更新/frontmatter 读写（`test_agent_memory.py` 或新建 `test_recall.py`）

## 2. 链路经验查询时结构化沉淀（复用 state，零额外计算）

- [ ] 2.1 新增 `persist_linkage_memory(mem_store, state)` 函数（在 `recall.py` 或新模块）：直接用 `state.current_tables` 两两组合（或从 `state.join_path_section` 解析表对），不重新扫 SQL
- [ ] 2.2 已存在的表对：`co_occurrence += 1`，若 JOIN 路径/场景新颖则追加；走 `save_memory(mem_id=existing)` 更新
- [ ] 2.3 新表对：创建 linkage 记忆，写入表对 + JOIN 路径（from join_path_section）+ 典型 SQL（截断）+ 场景（from thinking）+ co_occurrence=1
- [ ] 2.4 在 `_persist`（`chat_stream.py:819` 记忆提炼附近）插入链路沉淀块：条件 `state.success and state.sql and len(state.current_tables) >= 2 and memory_linkage_capture_enabled`，try/except 失败仅 WARNING 不阻塞
- [ ] 2.5 单测：多表成功查询 → linkage 记忆正确创建/更新；单表查询跳过；失败不阻塞其他反哺

## 3. 配置项（config.py，走环境变量）

- [ ] 3.1 新增 config 项：
  - `memory_linkage_capture_enabled: bool = True`
  - `graph_sync_on_consolidate_enabled: bool = True`
  - `graph_linkage_co_occurrence_threshold: int = 3`
  - `graph_linkage_new_pair_threshold: int = 5`
  - `graph_linkage_confidence_boost: float = 0.1`
  - `graph_feedback_discover_new_pairs: bool = True`
- [ ] 3.2 加入 `_validate_positive_int` 校验白名单（如适用）；补 `.env.example` 说明

## 4. 整理时轻聚合 + 图谱更新

- [ ] 4.1 新增 `apply_confidence_updates(content, updates: dict[tuple[str,str], float])`（在 `knowledge_graph.py`）：遍历 content.relationships 改 confidence，返回新 content（不就地改）
- [ ] 4.2 新增 `linkage_memories_to_cooccurrence(mem_store) -> dict[tuple[str,str], int]`：从 linkage 记忆 frontmatter 读 co_occurrence（轻聚合，不解析 SQL）
- [ ] 4.3 新增 `sync_linkage_to_graph(db, tenant_id, data_source_id)`：读 linkage → 筛达阈值表对 → 算 boost（+新表对发现）→ `apply_confidence_updates` → 写新 SemanticModel 版本
- [ ] 4.4 新表对发现：共现 ≥ `new_pair_threshold` 且不在 relationships 时，调单对推断加入，初始 confidence 0.5，source="implicit_mining"
- [ ] 4.5 在 `/memory/consolidate`（`memory.py` 整理动作末尾）追加调用 `sync_linkage_to_graph`，try/except 失败不影响整理本身
- [ ] 4.6 并发保护：图谱更新走单事务，SemanticModel 乐观锁 version 校验或 SELECT FOR UPDATE，冲突跳过
- [ ] 4.7 单测：已知关系 boost / 新关系加入 / version 递增 / 冲突跳过 / 图谱失败不影响整理

## 5. 链路经验参与记忆整合 + 双路召回

- [ ] 5.1 确认 `consolidate_memories` 能处理 linkage 类记忆（LLM 整理时合并低频表对）；验证 MEMORY.md 索引截断保护覆盖 linkage
- [ ] 5.2 确认 `recall_memories` 召回时能命中 linkage 记忆（content 注入 prompt 辅助 SQL 生成）；必要时调整召回逻辑不过滤 linkage 类型
- [ ] 5.3 单测：整理合并碎片化 linkage；召回命中 linkage 注入 prompt

## 6. 验收测试

- [ ] 6.1 端到端：3 次同表对成功查询 → linkage 记忆 co_occurrence=3 → 整理触发 → SemanticModel 新版本 confidence 提升
- [ ] 6.2 端到端：5 次未知表对共现 → 整理触发新关系发现 → 新 Relationship 加入（source=implicit_mining, confidence=0.5）
- [ ] 6.3 关闭开关：`memory_linkage_capture_enabled=false` → 成功查询不产生 linkage；`graph_sync_on_consolidate_enabled=false` → 整理不更新图谱
- [ ] 6.4 失败隔离：linkage 沉淀抛异常 → 查询正常返回 + 其他反哺仍执行；图谱更新抛异常 → 整理仍成功
- [ ] 6.5 零额外计算验证：mock 查询，断言沉淀过程不调用 SQL 正则/JOIN 重算（只读 state 字段）
- [ ] 6.6 全量测试通过：`.venv/bin/python -m pytest backend/tests/ -q`（538 passed 基线不回归）

## 7. 文档

- [ ] 7.1 更新 `doc/chatbi-v2/EVOLUTION-ROADMAP.md`：方向1 状态改为"已完成"，记录"复用记忆基建"的最终设计决策
- [ ] 7.2 `backend/.env.example` 补 memory_linkage_* / graph_linkage_* / graph_sync_* 配置项
- [ ] 7.3 CHANGELOG.md 新增"记忆与图谱集成闭环"条目
