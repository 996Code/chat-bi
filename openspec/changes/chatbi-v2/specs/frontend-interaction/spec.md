# Frontend Interaction

## Status: ADDED

## 概述

ChatBI v2 的前端不再只是"聊天面板 + 输入框"，而是对标 Claude Code 的双中枢架构：Messages（展示已经发生了什么）+ PromptInput（组织下一步要做什么）。Agent 思考过程通过 SSE 事件流式推送到前端。

## 需求

### FRT-001: 聊天界面

**对标 Claude Code**：Messages + VirtualMessageList — 虚拟滚动 + 搜索 + sticky prompt

核心组件：
1. **消息流**：用户消息 + Agent 回复 + 表格 + 图表
2. **思考链展开**：Agent 每一步（预思考 → 选表 → SQL → 自检 → 图表）可展开查看
3. **表格渲染**：大数据量分页 + 排序
4. **图表渲染**：ECharts 渲染完整 option JSON + 交互

**验收标准**：
- [ ] 消息流支持 SSE 流式输出（非一次加载）
- [ ] 思考链可逐步展开查看详情（每步的 LLM prompt + 输出 + 耗时）
- [ ] 图表支持 hover/缩放/下载

### FRT-002: Agent 暂停交互

**对标 Claude Code**：AskUserQuestionTool — 屏幕弹出表单

Agent 调用 ask_user 工具时：
- 前端弹出确认表单（非 Modal，不阻塞消息流）
- 表单内容由 Agent 动态生成（"请选择要查询的表" / "结果似乎不对，要继续吗？"）
- 用户输入 → 作为 tool_result 回流 → Agent 恢复执行

**验收标准**：
- [ ] Agent 暂停 → 前端展示确认表单 + Agent 的当前思考状态
- [ ] 用户选择后 → Agent 继续执行（SSE 流恢复）
- [ ] 确认表单可以在思考链中回溯查看

### FRT-003: Claude Code 式输入编排器

**对标 Claude Code**：PromptInput — 不是文本框，是会话控制台

输入框功能：
- 输入缓冲 + arrow key history（上下箭头浏览历史）
- 数据源切换（`/ds <name>` 或下拉选择）
- 快捷操作：`/chart bar` 切换图表、`/sql` 查看/编辑 SQL、`/explain` 解释
- Token 预算指示（当前用了多少 token / 剩余多少）

**验收标准**：
- [ ] 支持 arrow key 浏览输入历史
- [ ] 支持 slash command 快捷操作
- [ ] 显示 Token 预算

### FRT-004: Pipeline Trace 可视化

**对标 Claude Code**：BackgroundTasksDialog — 任务状态摘要 + ShellProgress 活动行

每次查询完成后：
- 展示完整 Agent 调用链（Intent → Schema → SQL → SelfCheck → Chart）
- 每步展示：耗时 + token 用量 + 状态（success/fail/retry）
- 支持展开每步的详细 prompt（对标 Claude Code dump-prompts）

**验收标准**：
- [ ] 查询完成后可点击展开 Pipeline Trace
- [ ] 每步展示耗时 + token 用量
- [ ] 自愈步骤展示："原始 SQL: xxx → 错误: 1054 → 修复后: xxx"

### FRT-005: 其他页面

- **数据源管理**：列表 + 添加/编辑/删除 + 健康状态指示
- **语义层编辑器**：JSON 查看 + 编辑 + 版本历史
- **Skills 编辑器**：Markdown 编辑 + 预览 + 版本管理
- **看板**：拖拽布局 + 从聊天保存图表 + 分享链接
- **查询历史 + 审计日志**：搜索 + 重跑 + 审计筛选

**验收标准**：
- [ ] 所有页面均可正常 CRUD
- [ ] 语义层编辑器展示 AI 推断置信度和来源
- [ ] Skills 编辑器支持 Markdown 预览