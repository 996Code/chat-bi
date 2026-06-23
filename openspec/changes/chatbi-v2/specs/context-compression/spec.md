# Context Compression

## Status: ADDED

## 概述

对标 Claude Code 的 compact + 状态补偿 + 熔断器机制。v2 不是简单截断对话，而是"压缩 + 补回关键状态"。触发条件为 Token 精确阈值（超过模型上下文上限的 70%）。

## 需求

### CMP-001: Token 阈值触发压缩

**对标 Claude Code**：有效窗口 = contextWindow - reservedForSummary(20K) - AUTOCOMPACT_BUFFER(13K)

触发条件：当前 LLM 上下文 token 超过模型上限的 70%
- 为什么不用轮次阈值：不同对话的 token 密度不同（带大 SQL 结果的比闲聊高得多），轮次阈值太粗糙
- 对标 Claude Code 的 `shouldAutoCompact()` 前置 13K buffer

**验收标准**：
- [ ] 上下文 token 超过 70% 上限 → 自动触发压缩
- [ ] 显示压缩进度（"正在总结历史对话..."）

### CMP-002: 压缩后状态补偿

**对标 Claude Code**：createPostCompactFileAttachments + getDeferredToolsDeltaAttachment

压缩不只是删旧消息，压缩后立即补回：
1. 语义层 context（当前查询用到的表和列的定义）
2. 当前 SQL + 筛选条件
3. 当前 Skills 规则
4. 结果摘要（最近 1 轮的关键数字）

压缩后的上下文面貌：
```
[历史摘要] 用户查了本月/上月各品类销售额，加入了退货率，当前展示柱状图
[最近 3 轮完整对话]
[状态补偿] 语义层: orders + products / 当前SQL: SELECT ... / 筛选: month='2026-06'
[当前用户问题]
```

**验收标准**：
- [ ] 压缩后 Agent 仍能正确回答"上个月呢"（知道当前在查 orders 表）
- [ ] 压缩后 Skills 规则仍然生效（GMV 仍按定义计算）

### CMP-003: 压缩摘要保留

**对标 Claude Code**：Session Memory 直挂 — 读后台提取的摘要文件

压缩后的旧轮次不是直接删除，而是：
1. 生成一句话摘要（"用户查了本月各品类销售额"）
2. 摘要可展开查看详情
3. 关键决策点标注（用户手动确认过的表选择、SQL 修改）

**验收标准**：
- [ ] 压缩后的对话摘要可点击展开查看原始内容
- [ ] 用户手动确认过的关键决策不丢失

### CMP-004: 熔断器

**对标 Claude Code**：连续 3 次 autoCompact 失败 → 熔断

压缩失败（Prompt Too Long 等）→ 熔断：
- 连续 3 次压缩失败 → 停止尝试压缩 → 给用户明确提示 "对话过长，建议新开对话"
- 对标 Claude Code 每天省下 250K 次死锁 API 调用

**验收标准**：
- [ ] 连续 3 次压缩失败 → 熔断 + 提示用户新开对话
- [ ] 熔断后不继续尝试压缩（不浪费 API 调用）