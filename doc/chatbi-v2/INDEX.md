# ChatBI v2 — 完整方案文档

> 这是 ChatBI v2 重构的完整方案文档包。包含 4 项总览文档 + 11 个领域规格文档 + 参考文档索引。
>
> 对标来源：Claude Code 源码深度解读（3043行）+ 海泰 ChatBI 完整代码分析 + WrenAI / Vanna / DB-GPT / SQLChat 竞品分析 + v1 48 条经验教训.
>
> 阅读顺序：先看 3 份核心参考 → 再看 4 项总览 → 按需深入 15 个领域规格.

---

## 一、参考文档（开发前必读，建立背景认知）

这些文档来自 v1 阶段积累和竞品分析，是本方案的设计依据：

| 序号 | 文档 | 路径 | 内容 | 阅读时长 |
|------|------|------|------|---------|
| R1 | **Claude Code 源码深度解读** | `../Claude-Code-源码深度解读.md` | 3043行完整分析：架构/Query循环/Tool体系/Prompt管理/Memory/压缩/Multi-Agent/安全/Skills | 90 分钟 |
| R2 | **海泰 ChatBI 完整代码分析** | `../海泰ChatBI完整代码分析.md` | 24 个 Python 文件 + 5 个 Skills 文件的逐行解析 | 60 分钟 |
| R3 | **v1 经验教训** | `../经验教训.md` | 48 条实战经验 + 19 条代码审计发现 | 30 分钟 |

### 项目背景文档（快速了解项目定位和规划）

| 序号 | 文档 | 路径 | 内容 |
|------|------|------|------|
| R4 | **项目概览** | `../项目概览.md` | 产品定位、核心价值、技术栈 |
| R5 | **规划文档** | `../规划文档.md` | 竞品分析（WrenAI/Vanna/DB-GPT/SQLChat）+ 架构设计 |
| R6 | **需求文档 v1** | `../需求文档.md` | 88 个 v1 需求清单 + 完成状态 |
| R7 | **路线图** | `../路线图.md` | v1 4 Phase 规划 |
| R8 | **方法论** | `../方法论.md` | 从 0 做项目完整方法论 |

---

## 二、v2 方案文档（必读，按顺序）

| 序号 | 文档 | 路径 | 内容 | 阅读时长 |
|------|------|------|------|---------|
| 1 | **Proposal** | `proposal.md` | 为什么要做、5条设计法则、对标分析（Claude+海泰）、P0/P1/P2 目标、成功指标、关键决策 | 10 分钟 |
| 2 | **Design** | `design.md` | 技术栈、Leader-Worker 架构图、Agent 执行流程（7步）、State Store、Prompt 分层、两阶段 RAG、Skills 分层、追问维度继承、复合指标展开、取舍表（11项）+ 风险缓解（8项） | 15 分钟 |
| 3 | **Tasks** | `tasks.md` | 54 个任务（T001-T054）、7 个 Phase、依赖关系图、每个 Phase 的冒烟测试 | 5 分钟 |
| 4 | **ROADMAP** | `ROADMAP.md` | 7 Phase 路线图（GSD 桥接） | 2 分钟 |

---

## 三、核心领域规格（按阅读顺序）

| 序号 | 领域 | 路径 | 对标来源 | 核心内容 | 阅读时长 |
|------|------|------|----------|---------|---------|
| 5 | **Agent 执行引擎** | `specs/agent-execution-engine/spec.md` | Claude query.ts + 海泰 Stage 状态机 | while(true)循环、自愈（10+错误码）、结果自检、上下文压缩、Checkpointer、Leader Stage 状态机、SQL 执行硬约束、JSON 自愈 | 10 分钟 |
| 6 | **语义层与知识图谱** | `specs/semantic-layer/spec.md` | WrenAI MDL + 海泰 ContextKey + Claude Memory | JSON Schema（Model/Relation/Metric/CalcField）、AI 自动推断、知识图谱演化、复合指标递归展开、宁缺毋滥策略、版本管理 | 8 分钟 |
| 7 | **RAG 向量检索** | `specs/rag-retrieval/spec.md` | 海泰 Milvus 两阶段检索 + Claude Relevant Recall | Milvus+BGE Embedding、两阶段检索（向量召回→LLM精筛）、相似度过滤（0.5）、语义缓存、Few-shot 匹配、精确查询 | 5 分钟 |
| 8 | **安全框架** | `specs/security-framework/spec.md` | Claude 双防线 + Trust 时序 + 降级告警 | Fail-Closed、三层校验（AST→危险函数→白名单）、框架级多租户、密钥安全、降级告警（WARNING/ERROR）、审计全覆盖、Unicode 清洗 | 7 分钟 |
| 9 | **意图识别** | `specs/intent-classification/spec.md` | 海泰 92行 prompt + Pydantic 强约束 | 5 种意图、Pydantic 强约束（confidence 降级）、追问维度继承（锚点覆盖/继承）、normalized_question 剥离可视化措辞 | 5 分钟 |
| 10 | **上下文压缩** | `specs/context-compression/spec.md` | Claude compact + 状态补偿 + 熔断器 | Token 阈值触发（70%）、压缩后状态补偿（语义层+SQL+筛选+Skills）、摘要保留、熔断器（3次连续失败） | 4 分钟 |

---

## 四、体验与交互规格

| 序号 | 领域 | 路径 | 对标来源 | 核心内容 | 阅读时长 |
|------|------|------|----------|---------|---------|
| 11 | **Agent 反思** | `specs/agent-reflection/spec.md` | Claude 预思考 + 追问优化 | 每次 SQL 前预思考（选表理由+聚合方式+注意事项）、追问时分析上一轮 SQL 优化空间 | 3 分钟 |
| 12 | **Skills 业务规则** | `specs/skills-business-rules/spec.md` | 海泰 SKILL.md + reference/*.md + Claude Skills | SKILL.md 格式（YAML frontmatter）、热更新、Prompt 注入、前端编辑器 | 4 分钟 |
| 13 | **反馈闭环** | `specs/feedback-loop/spec.md` | WrenAI Q-SQL Pair + Claude frustration detection | 4 维反馈（点赞/改SQL/纠正图表/评论）、审核后回流、负面信号自动触发 | 3 分钟 |
| 14 | **前端交互** | `specs/frontend-interaction/spec.md` | Claude Messages + PromptInput + Pipeline Trace | 聊天界面（SSE+思考链）、Agent 暂停交互 UI、Claude Code 式输入编排器（arrow history+slash+token预算）、Pipeline Trace 可视化 | 5 分钟 |
| 15 | **可观测性** | `specs/observability/spec.md` | Claude dump-prompts + /context + checkResumeConsistency | dump-prompts 导出完整 LLM 请求、/context 逐段 token 统计、Agent 调用链追踪、Checkpointer 恢复一致性审计 | 5 分钟 |

---

## 新对话启动指令

在新对话中，可以这样告诉 Claude Code：

```
我来继续做 ChatBI v2 重构。请按以下顺序读取 doc/chatbi-v2/ 下的方案文档：

1. 先读参考文档了解背景：
   - doc/Claude-Code-源码深度解读.md  (这是我们的设计参考——Claude Code的核心机制)
   - doc/海泰ChatBI完整代码分析.md    (竞品源码分析——Leader-Worker架构、Skills、Milvus)
   - doc/经验教训.md                  (v1的48条踩坑记录，别重蹈覆辙)

2. 再读 v2 方案文档理解目标：
   - doc/chatbi-v2/proposal.md        (5条设计法则 + P0目标10项)
   - doc/chatbi-v2/design.md          (技术栈 + Leader-Worker架构 + Agent执行流程)
   - doc/chatbi-v2/tasks.md           (54个任务 + 7个Phase + 依赖关系)

3. 最后按需读取领域 spec：
   - 先看 specs/agent-execution-engine/、semantic-layer/、rag-retrieval/、security-framework/
   - 再看 specs/intent-classification/、context-compression/
   - 最后看 specs/agent-reflection/、skills-business-rules/、feedback-loop/、frontend-interaction/、observability/

4. 从 Phase 1 开始，按照 tasks.md 的任务列表逐步实现
```
