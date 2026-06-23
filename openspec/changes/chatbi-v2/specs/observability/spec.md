# Observability

## Status: ADDED

## 概述

对标 Claude Code dump-prompts + /context + pipeline_trace + checkResumeConsistency 四大可观测机制。v2 需要能回答：实际发给 LLM 的 prompt 是什么、各 section 消耗多少 token、Agent 调用链每步耗时 + token、Checkpointer 恢复一致性。

## 需求

### OBS-001: dump-prompts 导出完整 LLM 请求

**对标 Claude Code**：`dump-prompts` — 拦截 API 请求，把完整 prompt + response 写到 `~/.claude/dump-prompts/<session>.jsonl`

每次 LLM 调用记录：
- system prompt 完整内容（含语义层 context + Skills + 约束）
- user prompt 完整内容（含用户问题 + 对话历史）
- LLM response 完整内容
- 时间戳 + 模型名 + token 消耗（input/output/total）

存储格式：JSONL 文件（对标 Claude Code append-only）

**验收标准**：
- [ ] 每次 LLM 调用的完整 prompt + response 可导出查看
- [ ] 支持按 session 筛选
- [ ] 可通过 UI 面板查看最近的 LLM 请求详情

### OBS-002: /context 逐段统计 token

**对标 Claude Code**：`/context` — 把 effective system prompt 拆成 named entries，逐段统计 token

统计维度：
- 语义层 context 消耗 token 数
- Skills 规则消耗 token 数
- 对话历史消耗 token 数（摘要 vs 完整轮次占比）
- Few-shot 示例消耗 token 数
- 用户问题消耗 token 数

展示形式：
- 前端可观测面板（Pipeline Trace 旁边）
- API 端点 `GET /observability/token-stats`

**验收标准**：
- [ ] 展示当前查询的各 section token 消耗
- [ ] 标注各 section 是否命中缓存
- [ ] 展示总剩余 token 预算

### OBS-003: Agent 调用链追踪

**对标 Claude Code**：pipeline_trace — 记录 Agent 调用链每步耗时+token

每次查询记录完整 Agent 调用链：
- Intent Classifier 耗时 + token
- Schema Searcher 耗时 + token（向量召回 vs LLM 精筛分别统计）
- SQL Agent 耗时 + token（生成 + 校验 + 执行 + 自愈分别统计）
- Self-check 耗时 + token
- Chart Agent 耗时 + token
- 每步 status（success/fail/retry）

**验收标准**：
- [ ] 查询完成后可查看完整 Agent 调用链
- [ ] 每步展示耗时 + token 用量 + 状态
- [ ] 自愈步骤展示原始错误 + 修复前后 SQL 对比

### OBS-004: Checkpointer 恢复一致性审计

**对标 Claude Code**：`checkResumeConsistency` — 恢复后校验 messageCount 与实际 chain 长度

Checkpointer 恢复状态后：
- 校验写入时记录的 messageCount 与实际恢复出的 chain 长度是否一致
- 监控"写入时的会话"和"恢复后读出的会话"是否发生漂移

**验收标准**：
- [ ] Checkpointer 恢复后自动校验一致性
- [ ] 不一致时记录 ERROR 日志 + 监控告警