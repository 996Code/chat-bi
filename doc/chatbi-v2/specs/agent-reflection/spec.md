# Agent Reflection

## Status: ADDED

## 概述

Agent 反思是对标 Claude Code 中 Tool 执行失败后的 error 回流 + compact 自检机制。4 个维度：预思考、自愈、结果自检、追问优化。

详细的自愈和结果自检流程见 `agent-execution-engine`。本 spec 专注意图识别中的预思考和追问中的主动优化。

## 需求

### REF-001: 生成前预思考

**对标 Claude Code**：LLM 调用工具前在 reply 中说明 "I'll do X because Y"

每次 SQL 生成前，Agent 输出预思考：
- 问题拆解：这个问题需要哪些维度和指标？
- 表选择理由：为什么选这张表？（"用户提到了'销售额'，匹配 orders 表的 total_amount 字段"）
- 聚合方式：需要 SUM/AVG/COUNT/复杂计算？
- 注意事项：可能的陷阱（Fan Trap 笛卡尔积风险、字段歧义等）

预思考内容在 SSE 流式中作为独立事件推送给前端（`event: thinking`），前端默认折叠、用户可展开查看。

**验收标准**：
- [ ] 每次 SQL 生成前，Agent 输出预思考内容（选表理由 + 聚合方式 + 注意事项）
- [ ] 预思考内容作为独立 SSE 事件推送，前端可展开查看
- [ ] 预思考不准确时，用户可以干预（"我觉应该用 orders 表而不是 sales 表"）

### REF-002: 追问时主动优化

**对标 Claude Code**：compact 后状态补偿 — Agent 知道"现在在做什么"

用户追问时，Agent 分析上一轮的 SQL 和结果：
- 上一轮 SQL 是否有优化空间？（用了全表扫描？可以加索引？）
- 新的问题是否需要更好的 SQL 写法？（追问"加上退货率"→ 之前是单表，现在需要 JOIN）
- 是否存在更好的可视化方式？（之前的柱状图数据标签被截断了 → 建议旋转角度）

Agent 主动建议改进，而不是等用户发现问题。

**验收标准**：
- [ ] 追问"加上退货率"→ Agent 提示"需要关联 refunds 表，我将修改 SQL 加入 LEFT JOIN"
- [ ] 追问"上个月呢"→ Agent 提示"我注意到上一轮 SQL 扫描了全表，建议加时间索引"
- [ ] 建议不作为阻塞（Agent 输出建议但继续执行），用户可以忽略