# Feedback Loop

## Status: PARTIAL — 裁剪后仅保留改 SQL + 审核回流

## 裁剪说明

| 功能 | 决策 | 理由 |
|---|---|---|
| 点赞/点踩 | **不纳入** | 轻量反馈价值有限，改 SQL 审核已覆盖核心需求 |
| 纠正图表 | **不纳入** | 非核心，图表偏好可通过 Skills 规则实现 |
| 写评论 | **不纳入** | 非核心，改 SQL 时可附带说明 |
| 负面信号自动触发 (FBK-003) | **不纳入** | 依赖点赞/点踩，随整体裁剪 |
| **改 SQL + 审核回流 (FBK-001/002)** | **考虑恢复** | 核心闭环：用户改 SQL → admin 审核 → 回流知识库 |

## 概述

对标 WrenAI 反馈闭环（Question-SQL Pair）+ Claude Code frustration detection + transcript sharing。v2 裁剪后仅保留核心闭环：用户直接改 SQL → admin 审核 → 回流知识库。点赞/点踩、纠正图表、写评论、负面信号自动触发不再纳入。

## 需求

### FBK-001: 反馈维度

| 维度 | 说明 | 对标 |
|------|------|------|
| **点赞/点踩** | 最轻量反馈 | Claude Code matchesNegativeKeyword — 廉价 frustration heuristic |
| **直接改 SQL** | 用户修改 Agent 生成的 SQL → 修改后的版本作为审核候选 | WrenAI "Adjust answer" |
| **纠正图表** | 用户换了图表类型 → 记录偏好 | Claude Code CHART_MODIFY 意图 |
| **写评论** | 用户说明为什么不对 / 哪里需要改进 | Claude Code FeedbackSurvey |

**验收标准**：
- [ ] 用户点踩 → 立即记录（不阻塞）
- [ ] 用户改 SQL → 保存原始版本 + 修改后版本（diff 可查看）
- [ ] 用户纠正图表类型 → 记录偏好到用户记忆

### FBK-002: 审核后回流

**为什么需要审核**：防止错误反馈污染知识库。对标 Claude Code 的记忆治理规则：过时/错误的记忆需要治理。

回流流程：
1. 用户提交反馈 → 进入 Review 队列
2. 审核者（admin 角色）查看：原始 SQL vs 用户修改的 SQL、原始图表 vs 用户选择的图表
3. 审核通过 → 回流到知识库（Question-SQL Pair + 更新语义层 confidence）
4. 审核拒绝 → 反馈关闭 + 拒绝原因

**验收标准**：
- [ ] 反馈数据进入 Review 队列（不可直接修改知识库）
- [ ] admin 可审核通过/拒绝
- [ ] 审核通过后，下次相似问题使用修正后的 SQL

### FBK-003: 负面信号自动触发

**对标 Claude Code**：processTextPrompt → matchesNegativeKeyword → logEvent → useFrustrationDetection → FeedbackSurvey

检测用户输入中的负面信号：
- 中文关键词匹配："不对"/"错了"/"没用"/"垃圾"/"差的远"/"完全不对"
- 连续 2 次点踩
- 触发 → 弹出友好反馈表单："看起来这个结果可能不太对？方便告诉我问题在哪吗？"

**验收标准**：
- [ ] 用户说 "这个完全不对" → 自动触发反馈表单
- [ ] 连续 2 次点踩 → 自动触发
- [ ] 反馈表单不强制（用户可以关闭）