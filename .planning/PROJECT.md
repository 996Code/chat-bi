# ChatBI v2

## 概述

ChatBI v2 是从零重构的自然语言转 BI 报表平台。核心转变：从 v1 的"一次问答出 SQL"到"Claude Code 式多轮交互 + Agent 反思"。

**技术栈**: Python 3.12 + FastAPI + LangGraph + PostgreSQL(Milvus) + Redis + Vue 3 + ECharts

**设计法则**（来自 Claude Code 源码分析）：
1. 执行引擎是 while(true) 不是一次调用
2. 安全默认是"否"（Fail-Closed）
3. "问用户"就是普通 Tool
4. 压缩是"压缩 + 状态补偿"
5. Prompt 分层可缓存

## 当前状态（2026-06-23）

- **Phase 1（基础设施）已完成**：11/11 任务实现，20 个单元测试通过
- **下一步**：Phase 2（语义层与知识图谱），从 T012（语义层 JSON Schema）开始
- 详见 `STATE.md`（状态详情）、`ROADMAP.md`（阶段进度）、`doc/chatbi-v2/tasks.md`（任务清单）