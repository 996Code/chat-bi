# Phase 5 计划：对话与上下文管理（T036-T039）

> 批准于 2026-06-24。4 个任务，有明确依赖链。
> **对标**: Claude Code §6 上下文压缩 + §5 Memory Relevant Recall

## 架构基础（已就绪）
- **Checkpointer** (`checkpointer.py`): JSONL append-only, save_turn/get_last_state — 已接入 /chat
- **AgentMemory** (`agent_memory.py`): 文件化记忆 + MEMORY.md 索引 — 建好未接入
- **AgentState** (`agent.py`): 已有 current_sql/schema_context/chart_option 字段
- **config**: compression_token_threshold=0.70, compression_keep_recent_turns=3, memory_max_recall_count=5

## 任务划分

### T036: State Store（地基，其他三个都依赖）

**实现**: `backend/app/ai/state_store.py`
- ConversationState: 结构化状态（current_tables/sql/filters/result_summary/chart_type）
- save_state / load_state / restore_from_checkpoint
- 接入 /chat：每轮 run_agent 后存 state，追问时恢复
- **追问维度继承**（对标海泰）: 追问"上个月呢"→ 从 state 读锚点 + 相对时间展开
- TDD: 测 save/load/restore + 追问继承

### T037: Relevant Recall（独立，接 agent_memory）

**实现**: `backend/app/ai/recall.py`
- recall_memories(question, max=5): 从 agent_memory 按相关性召回最多5条
- 接入 sql_agent: 召回的记忆注入 prompt 动态段（不灌全量）
- 对标 Claude Code §5.4: scanMemoryFiles → formatManifest → 轻量选择
- TDD: 测召回数量限制 + 相关性排序

### T038: 上下文压缩（依赖 T036 State Store）

**实现**: `backend/app/ai/compressor.py`
- should_compress(messages): 算 token，超 70% 返回 True
- compact(history, keep_recent=3): 旧轮次 LLM 生成摘要
- 状态补偿: 压缩后补回 semantic_context + current_sql + filters (从 State Store 读)
- 熔断器: 连续3次压缩失败停止（复用 sql_healer 的熔断器模式）
- 接入 /chat: 对话历史超阈值时触发
- TDD: 测 token 计算 + 压缩 + 状态补偿 + 熔断

### T039: 对话摘要保留（依赖 T038 压缩产出摘要）

**实现**: 扩展 `state_store.py`
- 压缩后的摘要存入 State Store（可展开查看）
- 关键决策点标注（用户确认过的表选择/SQL修改）
- API: GET /chat/conversations/{id}/summary
- TDD: 测摘要存储 + 关键决策标注

## 实施顺序
T036 (State Store 地基) → T037 (Recall, 独立) → T038 (压缩, 依赖 T036) → T039 (摘要, 依赖 T038)

## 不在本次范围
- 前端对话历史 UI（Phase 7）
- Skills 记忆（Phase 6）
- 跨设备同步（远期）

## 约束
- 所有魔法数字走 config（已就位）
- Claude Code §6.1: 压缩不是截断，是压缩+状态补偿
- §6.4: 压缩熔断器（连续失败停止）
- §5.4: Relevant Recall 按需召回不全量灌入
