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

- **Phase 1（基础设施）已完成** + **Phase 2 执行中**：12/54 任务完成，33 个单元测试通过
- **已完成**：T012（语义层 JSON Schema，Pydantic v2）
- **下一步**：T013（数据源扫描）或 T018（复合指标）
- 详见 `STATE.md`（状态详情）、`ROADMAP.md`（阶段进度）、`openspec/changes/chatbi-v2/tasks.md`（任务清单）