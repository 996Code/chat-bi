# Phase E1 执行计划：graph-feedback-loop（记忆与图谱集成）

> 上下文：`E1-CONTEXT.md`｜规格：`openspec/changes/graph-feedback-loop/specs/`
> 原则：**严格按 Wave 顺序，Wave 内可并行，Wave 间有依赖必须等**。每完成一个 Task 跑相关测试。

## Wave 1：基建（记忆扩展 + 配置，无外部依赖）

### Task 1.1 — linkage 记忆文件约定 + helper（type: tdd）
- **read_first**: `backend/app/core/agent_memory.py:121`（save_memory）/ `backend/app/api/memory.py:81`（_ensure_seed_memories）
- **acceptance**: 能创建 `linkage-{tableA}-{tableB}.md`，frontmatter 含 `type: linkage` / `co_occurrence` / `tables`；`get_linkage_memory(table_a, table_b)` 按文件名查回
- **actions**:
  - Red: 写测试——创建 linkage 记忆、读回 frontmatter、按表对查询
  - Green: 实现 `get_linkage_memory` helper + 文件名约定（表名字典序）
  - Refactor: 确认 `_validate_memory_name` 不拒绝 linkage 前缀

### Task 1.2 — 配置项（type: setup）
- **read_first**: `backend/app/core/config.py`（_validate_positive_int 白名单）
- **acceptance**: 4 个新 config 项有默认值，走环境变量，`.env.example` 已补
- **actions**:
  - 加 `graph_linkage_co_occurrence_threshold=3` / `graph_linkage_new_pair_threshold=5` / `graph_linkage_confidence_boost=0.1` / `graph_feedback_discover_new_pairs=True`
  - 加进 `_validate_positive_int` 校验白名单
  - 更新 `.env.example`

## Wave 2：链路沉淀（依赖 Wave 1）

### Task 2.1 — persist_linkage_memory 函数（type: tdd）
- **read_first**: `backend/app/ai/recall.py:138`（extract_memory_from_turn 模式）/ `chat_stream.py:365-375`（state 字段）
- **acceptance**: 输入 mem_store + state，直接用 current_tables/join_path_section/thinking 写入/更新 linkage 记忆；不调用 SQL 正则；已存在表对 co_occurrence+1，新表对创建
- **actions**:
  - Red: 测试——mock state（多表），调用后断言 linkage 记忆创建 + 字段正确；再次调用同表对断言 co_occurrence=2
  - Green: 实现 `persist_linkage_memory`，从 current_tables 两两组合（或解析 join_path_section），写记忆
  - Refactor: 抽取"表对提取"为独立函数，便于测试

### Task 2.2 — SSE persist_warning 事件协议（type: implement）
- **read_first**: `backend/app/api/chat_stream.py:170-183`（emit 函数）/ SSE 事件头注释 line 9-18
- **acceptance**: 新增 `persist_warning` 事件类型，携带 `{stage, error, conversation_id, question}`；stage 枚举 saved_query/fewshot/memory_extract/linkage；error 脱敏；在 complete 之前发送，complete 照常发；多个反哺失败可发多个
- **actions**:
  - 在 emit 支持新增 `persist_warning` 事件
  - 更新 SSE 事件头注释

### Task 2.3 — 改造 _persist 所有反哺点为 persist_warning 告知（type: tdd）
- **read_first**: `chat_stream.py:774-842`（SavedQuery/fewshot/记忆提炼/链路沉淀四个反哺点的 try/except）
- **acceptance**: 四个反哺点失败时都发 persist_warning（含对应 stage），不再静默 WARNING；complete 仍 success=true；不互相阻塞
- **actions**:
  - Red: 测试——mock 每个反哺点逐一抛异常，断言 SSE 流含对应 stage 的 persist_warning + complete success=true
  - Green: 改造四个反哺点的 try/except，except 里发 persist_warning 事件（替代 logger.warning）
  - 注意：链路沉淀是新增反哺点（Task 2.1 的接入），其他三个是改造现有
  - persist_warning 在 finally 的 complete 之前发出，注意 SSE 流顺序

### Task 2.4 — 前端 persist_warning 处理（type: implement）
- **read_first**: `frontend/src/views/ChatView.vue` handleSSEEvent（case 分支）
- **acceptance**: 收到 persist_warning，ElMessage warning toast 非阻塞展示，文案含 question 片段；多个可叠加或合并带计数
- **actions**:
  - handleSSEEvent 新增 `case 'persist_warning'`，调 ElMessage.warning
  - toast 文案："查询『{question前15字}』的后台保存失败（{stage}），不影响结果"

## Wave 3：图谱更新（依赖 Wave 1-2，需要 linkage 数据）

### Task 3.1 — apply_confidence_updates + 乐观锁（type: tdd）
- **read_first**: `knowledge_graph.py:373-420`（apply_feedback_signals）/ `db/models.py:135-153`（SemanticModel version）
- **acceptance**: 输入 content + updates + expected_version，改 confidence 写新版本；version 不匹配抛 VersionConflictError
- **actions**:
  - Red: 测试——正常更新 version+1；冲突抛 VersionConflictError
  - Green: 实现 `apply_confidence_updates`，UPDATE WHERE version=expected_version，rows=0 则抛冲突
  - 新增 `VersionConflictError` 异常类

### Task 3.2 — linkage_memories_to_cooccurrence + sync_linkage_to_graph（type: tdd）
- **read_first**: `knowledge_graph.py:315-370`（mine_implicit_relationships）/ Task 1.1 helper
- **acceptance**: 从 linkage 记忆读 co_occurrence 轻聚合；达阈值表对算 boost；新表对发现（≥new_pair_threshold 且不在 relationships）；冲突抛 VersionConflictError 不吞错
- **actions**:
  - Red: 测试——mock linkage 记忆，断言 boost 计算 + 新表对发现 + 冲突抛错
  - Green: 实现 `linkage_memories_to_cooccurrence` + `sync_linkage_to_graph`
  - Refactor: 复用 mine_implicit_relationships 的算法核心

### Task 3.3 — /memory/consolidate 接入 + 409 冲突响应（type: implement）
- **read_first**: `backend/app/api/memory.py:126`（consolidate 端点）
- **acceptance**: 整理后调 sync_linkage_to_graph；冲突返回 409 + 详情（current_version/expected_version/表对列表）
- **actions**:
  - consolidate 末尾调 sync_linkage_to_graph
  - 捕获 VersionConflictError → 返回 409 + 详情 JSON

### Task 3.4 — /memory/consolidate/retry 端点（type: implement）
- **read_first**: `memory.py:126`（consolidate 复用）
- **acceptance**: POST retry 基于最新 version 重算 boost 合并；仍冲突返回 409
- **actions**:
  - 新增 `POST /memory/consolidate/retry`，读最新 SemanticModel version，调 sync_linkage_to_graph

## Wave 4：前端冲突处理（依赖 Wave 3）

### Task 4.1 — 整理 409 冲突弹框（type: implement）
- **read_first**: 前端记忆管理页（找 SemanticView 或 memory 组件的整理按钮）
- **acceptance**: 整理返回 409 → 弹框展示冲突详情 + 三选项（重试/仅保留记忆/取消）；重试调 retry 端点
- **actions**:
  - 整理请求 catch 409 → ElMessageBox.confirm 展示冲突
  - 重试 → POST retry；仅保留记忆 → 确认当前；取消 → 回滚

## Wave 5：整合召回 + 验收（依赖 Wave 1-4）

### Task 5.1 — linkage 参与整合 + 召回（type: verify）
- **read_first**: `recall.py:244`（consolidate_memories）/ `recall.py:27`（recall_memories）
- **acceptance**: consolidate_memories 处理 linkage；recall_memories 命中 linkage 注入 prompt；MEMORY.md 截断保护覆盖 linkage
- **actions**:
  - 验证/调整 consolidate_memories 和 recall_memories 不过滤 linkage 类型
  - 补测试

### Task 5.2 — 端到端验收（type: verify）
- **acceptance**: 对照 spec.md 全部 Scenario 验证（8.1-8.6 in tasks.md）
  - 3 次共现 → 整理 → confidence 提升
  - 5 次未知表对 → 新关系发现
  - 反哺失败告知 → 各反哺点 mock 抛异常 → persist_warning（含 stage）+ complete 正常
  - 并发整理 → 409 → 弹框 → 重试成功
  - 零额外计算（只读 state）
  - 全量测试 538 passed 不回归
- **actions**: 写 e2e 测试脚本 / 手动验证 / 跑全量 pytest

### Task 5.3 — 文档（type: setup）
- **acceptance**: EVOLUTION-ROADMAP 方向1 标完成；CHANGELOG 加条目；.env.example 完整
- **actions**: 更新三个文档

## 风险与守卫
- **Wave 2 Task 2.3 风险**：persist_warning 发送时机（finally 内 complete 之前）—— 注意 SSE 流顺序，complete 必须最后
- **Wave 3 Task 3.1 风险**：乐观锁要测并发场景（mock 两个并发更新）
- **回归保护**：每个 Wave 结束跑 `pytest backend/tests/ -q` 确认 538 不回归
