# ChatBI v2 Proposal

## Summary

ChatBI v1 经 48 条经验教训总结和代码审计，发现核心架构存在根本性缺陷：线性管道无法支持 Agent 反思、SQL 生成"宁可错答也不拒答"、安全体系形同虚设、缺少语义层/RAG/记忆等核心能力。v1 实际落地成了 SQLChat 的简化版，与规划中对标 WrenAI 的目标差距巨大。

v2 将从零重构，核心转变基于 Claude Code 源码深度解读中提取的 **5 条设计法则**：

1. **执行引擎是 while(true) 不是一次调用** — Agent 循环执行：生成 SQL → 执行 → 自愈 → 自检 → 修正 → 再生成
2. **安全默认是"否"** — 所有操作默认需确认，只有显式声明安全的才自动放行
3. **"问用户"就是普通 Tool** — Agent 不确定时调用 ask_user 工具暂停，用户输入作为 tool_result 回流
4. **压缩是"压缩 + 状态补偿"** — 对话压缩后补回语义层 context + 当前 SQL + 筛选条件
5. **Prompt 分层可缓存** — 语义层/Skills 放在 boundary 前面（可缓存），用户问题/历史放在后面

## Motivation

### v1 代码审计发现（48 条经验教训）

**代码质量层面（19 条发现）**：
- 8 条高危：Fernet≈明文、Redis 降级无声、多租户靠手动 WHERE、SQL 校验不防 DML、API 透传异常……
- 8 条中危：200 行上帝函数、重复 State 定义、Fallback Prompt 丢规则、手动拼 SSE……
- 3 条低危：连接测试不原子、图表时间判断误判、审计只记成功不记失败……

**根本模式**：安全降级无声、代码耦合过度、审计不完整

### 竞品分析对比（WrenAI / 海泰 / Vanna / DB-GPT / SQLChat）

| 维度 | 我们的 v1 | 标杆（WrenAI/海泰/Claude Code） | 差距 |
|------|---------|-------------------------------|------|
| 语义层 | 无（每次 LLM 从原始 schema 理解） | WrenAI MDL JSON + 海泰 ContextKey | 巨大 |
| RAG | 无（关键词匹配） | 海泰 Milvus + BGE + 两阶段检索 + 宁缺毋滥 | 巨大 |
| 反馈闭环 | 做了但没用（feedback 不回流） | WrenAI Question-SQL Pair + Claude Code frustration detection | 大 |
| Agent 架构 | 线性管道 | 海泰 Leader-Worker（deepagents）+ Claude Code 三套 multi-agent | 大 |
| 业务规则 | 硬编码在 Prompt 里 | 海泰 Skills (SKILL.md + reference/*.md) 分层 + 热更新 | 大 |
| 安全 | 假安全（降级无声） | Claude Code 双防线 + 默认拒绝 + 熔断器 | 巨大 |
| 意图识别 | DataQuery/Other 二分类 + 字符串匹配 | 海泰 92行 prompt + Pydantic 强约束 + 追问维度继承 | 大 |

### Claude Code 我们学到的 + 海泰我们学到的

**Claude Code（5 条设计法则 + 8 个核心机制）**：
- while(true) 循环、安全默认(Fail-Closed)、AskUserQuestion 即 Tool、压缩+状态补偿、Prompt 分层可缓存
- Memory 文件化、Relevant Recall（按需召回）、熔断器、Session Memory 直挂、dump-prompts 可观测

**海泰（7 个核心机制）**：
- 宁缺毋滥（错误数据比无数据危险）、Leader Stage 严格状态机（上游失败→final(failed)）
- 复合指标递归展开（MetricContext metric_type single/composite）
- 追问维度继承（normalized_question = 锚点未重写维度 + 本轮新增维度）
- 两阶段检索 + LLM 精筛 prompt（"候选来自向量检索，可能存在假阳性"）
- JSON 自愈（统计括号差异 → 补全）、Skills 分层（SKILL.md + reference/*.md）
- 单次执行硬约束（execute_sql 最多1次 + 自愈2轮）

## Goals

### v2 的核心目标（按优先级）

**P0（必须做，不做没价值）**：
1. Agent 执行循环：生成 SQL 前预思考 → 生成 → 执行 → 失败时自愈 → 成功时自检
2. 语义层 + RAG 检索：知识图谱 AI 自动推断 + Milvus 向量检索 + 人工校验
3. SQL 安全校验：三层防线（AST 拒绝非 SELECT + 危险函数拒绝 + 白名单列名校验）
4. 多租户框架级强制：SQLAlchemy session event 自动注入 tenant_id
5. Skills 业务规则外置：SKILL.md 热更新，改规则不改代码
6. 上下文压缩 + 状态补偿：Token 阈值触发，压缩后补回语义层 context
7. Agent 记忆文件化：每个 Agent 有独立记忆目录(.md 文件)，可查看/编辑/删除
8. 会话持久化 + Checkpointer：PostgreSQL 持久化每轮对话状态，支持跨会话恢复
9. Prompt 分层缓存：语义层/Skills 可缓存，用户问题/历史不可缓存
10. ask_user 关键节点暂停：Agent 不确定时调用 ask_user 工具暂停等待用户确认

**P1（应该做，显著提升体验）**：
11. 反馈闭环：点赞/改 SQL/纠正图表 → 人工审核 → 回流知识库
12. Relevant Recall：按需召回最多 5 条记忆，不全量灌入 prompt
13. 复合指标支持：指标可引用子指标（如"客单价 = GMV / 订单数"）
14. 可观测性：dump-prompts 导出完整 LLM 请求 + /context 逐段统计 token

**P2（可以做，锦上添花）**：
15. Pipeline Trace 可视化：展示 Agent 调用链每步耗时 + token 用量
16. 负面信号检测：检测用户输入中的挫败感信号 → 触发反馈收集

## Non-Goals

- Oracle / ClickHouse 支持（后续方言扩展）
- Java 注解导出（低优先级）
- 移动端 App（Web-first 响应式即可）
- OAuth/SSO 登录（邮箱密码够用）
- 模型路由（简单/复杂）（成本优化，准确率优先）
- CopilotKit 前端（自建）
- 实时 WebSocket 推送（SSE 流式即可）

## Success Metrics

| 指标 | 目标 | 测量方法 |
|------|------|---------|
| SQL 生成准确率 | ≥ 70%（单表聚合） | 内部测试集 50 个 QA 对 |
| Schema Linking 准确率 | ≥ 85% | 同测试集，LLM 选对的表/字段比例 |
| 安全：多租户数据泄露 | 0 次 | 跨租户查询测试 |
| 安全：SQL 注入/DML | 0 次执行 | DROP/UPDATE/DELETE + 危险函数全被拦截 |
| 自愈成功率 | ≥ 50%（语法/执行错误） | 错误 SQL 样本自愈后执行成功比例 |
| 冷启动时间 | ≤ 5 分钟 | 新数据源接入到首次可查询 |
| 平均查询响应 | ≤ 8 秒（无缓存） | P50 测量 |
| 缓存命中响应 | ≤ 500ms | 精确缓存命中 |

## Risks

| 风险 | 影响 | 缓解 |
|------|------|------|
| Milvus 向量检索精度不够 | 高 | 保留 LLM 二次精筛作为兜底 |
| Agent 反思循环可能导致 token 消耗过大 | 中 | 熔断器（连续失败 3 次停止）+ 2 轮自愈上限 |
| 知识图谱 AI 推断可能产生错误关系 | 中 | 人工审核机制 + 反馈数据回流前人工审核 |
| 上下文压缩后丢关键信息 | 高 | 压缩后状态补偿（补回语义层+当前 SQL+筛选条件） |
| v2 开发周期可能过长 | 高 | P0 先交付（核心 AI 管线 + 安全），P1/P2 后续迭代 |

## Key Design Decisions

| 决策 | 选择 | 放弃 | 依据 |
|------|------|------|------|
| 执行引擎 | while(true) 循环 | 线性管道 | Claude Code query.ts — 工具执行是循环的延续 |
| Agent 反思自动化程度 | 自动自愈 + 结果提示用户 | 全自动循环 | 用户确认的关键决策由人工把关 |
| 知识图谱生成 | 表结构推断 + 历史查询挖掘 + 反馈演化 | 纯手动配置 | 降低冷启动门槛 |
| 反馈回流时机 | 审核后回流 | 即时回流 | 防止错误反馈污染知识库 |
| 上下文压缩触发 | Token 阈值（超模型上限 70%） | 轮次阈值 | 更精准 不是猜 |
| 暂停触发 | Schema 不确定 + 结果异常时 | 每次 SQL 执行前 | 减少无用确认 |
| Agent 框架 | 自建轻量封装（取 deepagents 思路） | deepagents 三方依赖 | 可控性 |
| 元数据库 | PostgreSQL 一站式 | MySQL + PG 双库 | Milvus + Checkpointer 一站搞定 |
| 向量数据库 | Milvus | Milvus | 部署简单 |
| 图表生成 | LLM 声明式 ECharts option | 规则推断 | 可交互、可修改、不误判 |