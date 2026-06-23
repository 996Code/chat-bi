# Feedback Loop

## Status: ADDED

## 概述

对标 WrenAI 反馈闭环（Question-SQL Pair）+ Claude Code frustration detection + transcript sharing。v2 支持全方位反馈：点赞/点踩 + 直接改 SQL + 纠正图表 + 写评论。反馈需人工审核后回流到知识库，防止错误反馈污染。

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