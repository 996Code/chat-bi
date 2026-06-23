# Agent Execution Engine

## Status: ADDED

## 概述

Agent 不是一次 LLM 调用生成 SQL，而是 while(true) 循环：生成 SQL → 校验 → 执行 → 自愈 → 自检 → 修正 → 再生成。对标 Claude Code query.ts 主循环。

## 需求

### AEE-001: while(true) 执行循环

Agent 执行流程：
1. 组装上下文（用户问题 + 语义层 context + Skills + 对话历史 + State Store）
2. 预思考：分析问题需要哪些表/聚合/筛选 → 输出预思考内容
3. 调用 LLM 生成 SQL
4. AST 三层校验（SELECT 类型 + 危险函数 + 白名单列名）
5. 校验通过 → 执行 SQL
6. 执行失败 → AEE-002 自愈
7. 执行成功 → AEE-003 结果自检
8. 结果正常 → 生成图表 → 返回结果
9. 结果异常 → 提示用户（自动修正后仍异常）
10. 压缩检查 → AEE-004 上下文压缩

**对标 Claude Code**：query.ts 的 while(true) 循环 — "调 API → 执行工具 → 回流结果 → 再调 API"

**验收标准**：
- [ ] Agent 能在一次查询中多次调用 LLM（生成→执行→反思→修正→再生成）
- [ ] 每次 LLM 调用的完整 prompt + response 可导出查看（dump-prompts 式 JSONL）
- [ ] Agent 调用链每步耗时 + token 用量可查看

### AEE-002: 执行失败自愈

**对标 Claude Code**：Tool 执行失败 → tool_result error → LLM 分析 → 重新调用

触发条件：SQL 执行报错（MySQL/PostgreSQL 错误码）

自愈流程：
1. 错误码提取 + 中文描述映射（10+ 错误码：1146 表不存在/1054 列不存在/1064 语法错误/1052 歧义列……）
2. 构造专项纠正 prompt（列不存在→提供可用列列表、表不存在→提供可用表列表、歧义列→提示加表前缀）
3. LLM 重新生成 SQL（自愈 prompt 保留全部安全规则：只允许 SELECT、白名单列名）
4. 重新走三层校验 + 执行
5. 最多 2 轮自愈

**熔断器**：连续自愈失败 3 次（跨多次查询）→ 停止 + 返回明确错误 + ERROR 日志

**关键约束**：
- 自愈 prompt 的安全标准不能低于主 prompt（v1 教训：自愈 prompt 只有"你只生成 SQL，不解释"）
- 自愈生成的 SQL 经过与主路径相同的三层校验

**验收标准**：
- [ ] MySQL 错误 1054（列不存在）→ Agent 分析 + 提供可用列列表 → 重新生成正确的 SQL
- [ ] MySQL 错误 1052（歧义列）→ "请使用表名前缀，可用列: orders.amount, products.amount"
- [ ] 自愈不会生成 UPDATE/DELETE 来"修复" SELECT 错误
- [ ] 2 轮自愈失败 → 返回 "SQL 修复失败（已重试 2 轮），原始错误: xxx"
- [ ] 跨查询连续 3 次自愈失败 → 熔断 + ERROR 日志

### AEE-003: 结果自检

**对标 Claude Code**：compact 自检 — 压缩后检查结果是否合理

触发条件：SQL 执行成功

自检维度：
- 返回 0 行：可能存在条件过严 / 筛选条件逻辑错误
- 数值异常大/小：可能聚合方式错误 / JOIN 产生笛卡尔积
- 数据与预期不一致：可能选错了表或列

自检流程：
1. Agent 审视执行结果
2. 正常 → 继续生成图表
3. 异常 → 分析可能原因 → 尝试自动修正（修正条件/聚合方式）→ 重新执行
4. 自动修正后仍异常 → 调用 ask_user 工具暂停（"结果似乎不对：xxx，要继续还是重试？"）

**验收标准**：
- [ ] 返回 0 行 → Agent 输出 "结果为空，可能原因：时间筛选过严 / 条件逻辑错误，建议检查 xxx"
- [ ] 数值异常大（如日销售额 > 年销售额）→ Agent 怀疑聚合方式或 JOIN 问题 → 自动修正
- [ ] 异常时展示 Agent 的分析过程（为什么觉得不对？依据是什么？）

### AEE-004: 上下文压缩 + 状态补偿

**对标 Claude Code**：autoCompact + createPostCompactFileAttachments — 压缩后补回当前状态

触发条件：LLM 上下文 token 超过模型上限的 70%（精确计算，不猜轮次）

压缩策略：
1. 保留最近 3 轮完整对话
2. 旧轮次用 LLM 生成一句话摘要
3. 压缩后状态补偿：
   - 重新注入语义层 context（当前查询用到的表和列）
   - 重新注入当前 SQL + 筛选条件
   - 重新注入 Skills 规则

**验收标准**：
- [ ] 对话超过 token 阈值后自动触发压缩
- [ ] 压缩后 Agent 仍能正确回答新问题（追问"上个月呢"时知道当前在查 orders 表）
- [ ] 压缩后用户能看到被压缩轮次的摘要（可展开查看详情）
- [ ] 状态补偿：压缩后 Agent 知道当前 SQL 是什么、选了什么表

### AEE-005: 对话状态管理

**对标 Claude Code**：PostgreSQL Checkpointer + PostgresStore

**State Store（结构化状态）**：每轮对话结束后，Agent 将结构化信息写入 Store：
- current_tables: 当前选中的表和列
- current_sql: 当前 SQL
- current_filters: 当前筛选条件
- result_summary: 结果摘要（行数 + 关键数字，不是完整数据集）
- chart_type: 当前图表类型

**Checkpointer（会话持久化）**：每轮对话状态持久化到 PostgreSQL
- 追问时从 Checkpointer 恢复上轮状态
- 跨会话恢复（关闭页面再打开，继续之前的对话）
- 可以恢复到任意一轮查询的状态

**验收标准**：
- [ ] 追问"按品类拆分"→ Agent 从 Store 读到当前在查 orders 表 + 本月销售额
- [ ] 关闭页面再打开 → 恢复之前的对话上下文
- [ ] 可以查看历史对话的任意一轮状态

### AEE-006: Leader Stage 严格状态机（对标海泰 prompt.py Stage 迁移规则）

**对标海泰**：`intent → metric_search → metric_explain → generate_sql → execute_sql → visual → final`

Agent 执行链路是严格的状态机：
- **只能按顺序前进**，禁止跨越关键阶段
- **上游失败（ok=false）必须直接进入 final(failed)**，不继续执行
- intent=GENERAL 时直接进入 final
- intent=EXPLANATION 时在解释后进入 final

**对标海泰 Leader prompt Section 2**：
```
硬规则：
- 仅可按顺序前进，禁止跨越关键阶段
- 上游失败（ok=false）必须直接进入 final(failed)
```

**验收标准**：
- [ ] Schema 检索失败 → 直接返回错误，不继续生成 SQL
- [ ] SQL 校验失败 → 回到 SQL Agent 重试（不跳过校验直接执行）
- [ ] 自愈 2 轮仍失败 → 直接进入 final(failed)

### AEE-007: SQL 执行硬约束（对标海泰 execute_sql_tool 单次执行）

**对标海泰**：`execute_sql_tool 最多调用 1 次，成功后立即返回，不得再调用任何工具`

- SQL 执行成功 → 立即返回结果，不继续调用其他工具
- SQL 执行失败 → 触发自愈（最多 2 轮），自愈后的 SQL 重新执行
- 对标海泰的考量：防止 LLM 反复执行不同 SQL 消耗数据库资源

**验收标准**：
- [ ] SQL 执行成功后 Agent 不再重复执行
- [ ] 自愈 2 轮后不再重试

### AEE-008: 图表 JSON 自愈（对标海泰 visualization_agent 补全右括号）

**对标海泰**：`open_count = echarts_option.count("{") - close_count = echarts_option.count("}") → 补全缺失的右括号`

LLM 输出 ECharts option JSON 时可能被 max_tokens 截断 → JSON 不完整。最常见的截断模式是缺失右花括号。

自愈机制：
1. JSON 解析失败 → 统计左右括号数量差异
2. 左括号 > 右括号 → 补全缺失的右括号 → 重新解析
3. 仍失败 → 降级为规则推断图表类型

**验收标准**：
- [ ] LLM 输出的 JSON 缺失右括号 → 自动补全 → 正常渲染
- [ ] JSON 自愈失败 → 降级为规则推断 + WARNING 日志