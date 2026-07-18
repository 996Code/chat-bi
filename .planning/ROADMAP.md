# 路线图

## 里程碑 1：ChatBI v2 核心交付

| 阶段 | 名称 | 状态 | 描述 |
|------|------|------|------|
| 1 | 基础设施 | ✅ 已完成 | 项目骨架 + 认证 + 多租户 + 审计（11 任务，20 测试通过） |
| 2 | 语义层与知识图谱 | ⏳ 进行中 | MDL JSON + AI 推断 + 版本管理（T012 起） |
| 3 | RAG 检索与向量化 | 待规划 | Milvus + BGE + 两阶段检索 |
| 4 | Agent 执行引擎 | 待规划 | while(true) 循环 + 自愈 + 自检 + 暂停 |
| 5 | 对话与上下文管理 | 待规划 | State Store + 压缩 + Prompt 分层 |
| 6 | Skills 与反馈系统 | 待规划 | SKILL.md 热更新 + 审核回流 |
| 7 | 前端与交付 | 待规划 | SSE 聊天 + 思考链 + Trace + 一键部署 |

## 进度指标

- **已完成任务**: 15 / 54（Phase 1 全部 + Phase 2 T012-T014 + T018）
- **测试**: 108 passed，覆盖率 88%（门禁 75%，`pytest --cov`）
- **当前焦点**: Phase 2 执行中 — 下一步 T016（知识图谱推断）+ T017（演化）

> 注：里程碑 1 的 7 个 Phase 已全部完成（详见 STATE.md），上方表格为历史规划快照。

---

## 里程碑 2：ChatBI v2 演进规划（核心交付后）

基于 `doc/chatbi-v2/EVOLUTION-ROADMAP.md` 的 13 个演进方向，严格单点推进。

| 阶段 | 名称 | 状态 | 描述 |
|------|------|------|------|
| E1 | graph-feedback-loop | 🔄 规格已定义 | 成功查询反哺知识图谱 confidence（隐式信号闭环） |
| E2 | (待选) | 待规划 | 从 EVOLUTION-ROADMAP.md Wave 1/2 中选下一个 |

### 当前活动：E1 graph-feedback-loop

- **OpenSpec change**: `openspec/changes/graph-feedback-loop/`
- **规格文件**: proposal.md ✓ / design.md ✓ / specs ✓ / tasks.md ✓
- **下一步**: 运行 `/ai:plan graph-feedback-loop` 将规格转为执行计划
