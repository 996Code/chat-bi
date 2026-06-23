# ChatBI v2 Design

## 设计参考来源

| 来源 | 贡献 |
|------|------|
| **Claude Code 源码分析（3043行）** | 5 条设计法则、while(true)执行循环、安全默认、AskUserQuestion 即 Tool、Prompt 分层缓存、压缩+状态补偿、Memory 文件化、Relevant Recall、熔断器 |
| **海泰 ChatBI 源码分析** | 宁缺毋滥策略、Leader Stage 严格状态机、复合指标递归展开、追问维度继承、normalized_question 剥离可视化措辞、JSON 自愈、data_type 约束、两阶段检索 prompt 设计、Skills 分层（SKILL.md + reference/*.md） |
| **WrenAI 竞品分析** | MDL 语义层、三层 RAG、反馈闭环（Question-SQL Pair）、三层管道（Indexing/Ask/Follow-up）、Column Pruning |
| **Vanna AI / DB-GPT / SQLChat** | RAG 驱动 Text-to-SQL（Vanna）、多 Agent 分工（DB-GPT）、反面教材：无语义层无自愈（SQLChat） |
| **v1 48 条经验教训** | 安全降级无声、多租户框架级强制、自愈 prompt 需保留安全规则、审计全覆盖、Prompt 不能自相矛盾、魔法数字集中配置…… |

## 技术栈

| 层级 | 技术 | 理由 | 对标 |
|------|------|------|------|
| 后端 | Python 3.12, FastAPI | 异步原生 | — |
| AI 框架 | LangGraph StateGraph + 自建 Agent 封装 | 取 deepagents Leader-Worker 思路,不依赖三方 | Claude Code query.ts + 海泰 deepagents |
| 向量数据库 | **Milvus** | 专业向量检索,内置 Embedding Function,HNSW 索引 | 海泰同款 |
| Embedding | BGE-large-zh-v1.5 (1024 维) | 中文语义最优 | 海泰同款 |
| 元数据库 | PostgreSQL | 业务数据 + Checkpointer + Store | 海泰 PostgreSQL 双角色 |
| 缓存 | Redis 7 | 精确缓存 + 语义缓存 + 限流 + 登录锁 | — |
| SQL 校验 | SQLGlot | AST 解析 + 多层校验 | — |
| 前端 | Vue 3 + TypeScript + ECharts + Pinia | 声明式图表 | 海泰 CopilotKit → 我们自建 |
| 部署 | Docker Compose + Nginx + supervisord | 一键部署 | — |

## 核心架构：Leader-Worker 多 Agent + Store 通信

**对标**：海泰 deepagents Leader-Worker + Claude Code subagent/teammate + WrenAI 线性管道

```
┌─────────────────────────────────────────────────────────────────────┐
│ Leader Agent                                                        │
│ 职责: 意图分流 + Stage 状态机控制 + 子 Agent 委派 + 结果汇总         │
│                                                                     │
│ Stage 状态机（对标海泰 prompt.py Section 2）:                        │
│   intent → schema_search → generate_sql → execute_sql →             │
│   self_check → visualize → final                                    │
│   硬规则: 仅可按顺序前进,上游失败→直接 final(failed)                 │
│                                                                     │
│ tools: [read_state, write_state, ask_user, now_time]                │
│ sub_agents: [intent_classifier, schema_searcher,                    │
│              sql_agent, chart_agent]                                │
└─────────────────────────────────────────────────────────────────────┘
                    │
        ┌──────────┬┴─────────┬──────────┐
        ▼          ▼          ▼          ▼
┌────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│ Intent     │ │ Schema   │ │ SQL      │ │ Chart        │
│ Classifier │ │ Searcher │ │ Agent    │ │ Agent        │
├────────────┤ ├──────────┤ ├──────────┤ ├──────────────┤
│ 5意图+Pydantic│ Milvus  │ │ 5步流程   │ │ ECharts JSON │
│ 强约束     │ │ 两阶段检索 │ │+白名单    │ │ +Skills约束  │
│ confidence │ │ 宁缺毋滥  │ │+data_type │ │ +JSON自愈    │
│ 降级       │ │ 相似度过滤 │ │+单次执行  │ │ +降级规则    │
└────────────┘ └──────────┘ └──────────┘ └──────────────┘
        │          │          │          │
        └──────────┴──────────┴──────────┘
                          │
                          ▼
          ┌───────────────────────────────┐
          │   State Store (PostgreSQL)     │
          │   Namespace: (tenant, thread)  │
          │   对标 海泰 PostgresStore +    │
          │        Claude Checkpointer     │
          └───────────────────────────────┘
```

**为什么用 Store 通信而非直接调用？**
- **对标海泰**：子代理之间不直接通信 → 通过 Store 读写 → 解耦、可恢复、可调试、可扩展
- 如果 SQL 执行失败 → 从 Store 读取 schema_context 重新调用 sql_agent → 无需重新检索 schema

## Agent 执行流程（对标海泰 Stage 状态机 + Claude Code while(true)）

```
用户输入 "本月各品类销售额，给我看柱状图"
  │
  ▼
┌─────────────────────────────────────────────────────────────────┐
│ ① Intent Classifier                                              │
│    输入: 用户问题 + 对话历史 + 锚点上下文（如有）                  │
│    输出: Pydantic IntentOutput (intent + action + normalized_q   │
│          + confidence + chart_hint)                              │
│    对标: 海泰 92行 system_prompt + IntentClassifierOutput       │
│    关键: 剥离可视化措辞 → normalized_question = 纯业务问题        │
│    SSE: event=intent                                             │
├─────────────────────────────────────────────────────────────────┤
│ ② Pre-thinking                                                   │
│    "需要 orders 表 + SUM(total_amount) + 按 category 分组         │
│     + 时间筛选本月                                                │
│     ⚠ 注意: orders 表有 status 字段，GMV 应排除 cancelled 订单"   │
│    SSE: event=thinking                                            │
├─────────────────────────────────────────────────────────────────┤
│ ③ Schema Searcher                                                │
│    阶段1: Milvus 向量召回 top-20 + 相似度过滤(score >= 0.5)     │
│    阶段2: LLM 精筛 — "候选来自向量检索，可能存在假阳性"           │
│    宁缺毋滥: 无匹配 → 返回空 → Leader 进入 CLARIFICATION          │
│    SSE: event=schema                                              │
├─────────────────────────────────────────────────────────────────┤
│ ④ SQL Agent (5步流程 对标海泰 sql_agent)                         │
│    Step 0: 读取 Skills (SKILL.md) — 必须在生成 SQL 前             │
│    Step 1: 列筛选 (LLM 精简列 对标海泰 search_schemas_tool)       │
│    Step 2: 生成 SQL (白名单列名 + data_type约束)                  │
│    Step 2.5: Skills 自检 (对照规则清单逐条检查)                    │
│    Step 3: 校验 (三层: AST→危险函数→白名单)                       │
│    Step 4: 执行 (最多1次 + 自愈最多2轮)                           │
│    SSE: event=sql                                                 │
├─────────────────────────────────────────────────────────────────┤
│ ⑤ Self-check                                                     │
│    返回0行? 异常数字? → 分析 → 自动修正 → 仍异常 → ask_user      │
│    正常 → 继续                                                    │
│    SSE: event=self_check                                          │
├─────────────────────────────────────────────────────────────────┤
│ ⑥ Chart Agent                                                    │
│    LLM 生成完整 ECharts option JSON + Skills 图表约束             │
│    JSON 解析失败 → 统计括号差异 → 补全 → 重试                     │
│    仍失败 → 降级为规则推断                                        │
│    SSE: event=chart                                               │
├─────────────────────────────────────────────────────────────────┤
│ ⑦ Compression Check                                              │
│    Token > 70%? → compact + 状态补偿 (语义层+SQL+筛选+Skills)     │
│    SSE: event=complete                                            │
└─────────────────────────────────────────────────────────────────┘
```

## State Store 结构（对标海泰 ContextKey 7 枚举 + Claude Checkpointer）

```python
class ConversationState(TypedDict):
    # 当前查询上下文（对标海泰 metric_context + tables_context）
    current_tables: list[str]          # ["orders", "products"]
    current_columns: dict[str, list]   # {"orders": ["id", "total_amount"]}
    current_sql: str | None            # 对标海泰 sql_context
    current_filters: dict              # {"month": "2026-06"}
    result_summary: str | None         # "12品类,总额¥2.3M" 
    chart_type: str                    # "bar"
    
    # 语义层上下文（追问时复用，对标海泰 zip_table_context）
    schema_context: str                # 当前用到表的 DDL + 语义定义
    skills_applied: list[str]          # ["gmv_rule", "date_format"]
    
    # 对话元数据（对标海泰 intent_history — 唯一跨查询持久）
    turn_count: int                    # 5
    summary_chain: list[str]           # 每轮摘要
    anchor_question: str | None        # 上轮 normalized_question（追问锚点）
    anchor_filters: dict | None        # 上轮筛选条件（用于维度继承）

# Namespace 设计（对标海泰 PostgresStore 双层命名空间）:
#   (tenant_id, conversation_id) → 租户隔离 + 会话隔离
```

## Prompt 分层设计

**对标**：Claude Code systemPromptSections + DYNAMIC_BOUNDARY + section 级缓存

```
┌────────────────────────────────────────┐
│ 静态段（可缓存 5 分钟）                 │
│ - 系统身份: "你是 ChatBI Agent..."      │
│ - 语义层 context                       │
│ - Skills 规则（对标海泰 SKILL.md）       │
│ - SQL 约束（白名单/类型/方言）           │
├────────────────────────────────────────┤
│ PROMPT_DYNAMIC_BOUNDARY                │ ← 缓存边界（对标 Claude Code）
├────────────────────────────────────────┤
│ 动态段（每轮重算）                     │
│ - 用户问题 + normalized_question       │
│ - 对话历史（摘要 + 最近 3 轮）          │
│ - State Store 上下文                   │
│ - Few-shot 示例（最多 3 条）           │
└────────────────────────────────────────┘
```

## 两阶段 RAG 检索（对标海泰 metric_searcher + Claude Relevant Recall）

```
阶段 1: 向量召回
  normalized_question → BGE Embedding (1024维) → Milvus 余弦相似度
  → top-20 + score >= 0.5 过滤（对标海泰 filter score >= 0.5）

阶段 2: LLM 精筛
  prompt: "候选列表来自向量检索，可能存在假阳性。请判断语义是否真正匹配"
  → 宁缺毋滥: 无匹配返回空（对标海泰 metric_selection.py）

阶段 3: 精确查询（对标海泰 table_service + column_service）
  选中的表 → 标量精确查询 → 批量获取列信息 + data_type
```

## Skills 分层设计（对标海泰 SKILL.md + reference/*.md）

```
skills/
├── sql-rules/
│   ├── SKILL.md              # SQL 生成通用规则（白名单/类型约束/方言）
│   └── reference/
│       ├── mysql.md          # MySQL 方言专属规则
│       └── postgresql.md     # PostgreSQL 方言专属规则
├── chart-rules/
│   ├── SKILL.md              # 图表生成通用规则（颜色/交互/导出）
│   └── reference/
│       ├── bar.md            # 柱状图 ECharts option 模板
│       ├── line.md           # 折线图 ECharts option 模板
│       └── pie.md            # 饼图 ECharts option 模板
└── business-rules/
    └── SKILL.md              # 业务领域规则（GMV 定义/时间格式/非空规则）
```

**对标海泰分层思路**：SKILL.md 定义通用规则（所有子 Agent 共享），reference/*.md 定义类型专属配置（按需读取）。

## 意图识别 — 追问维度继承（对标海泰 Leader prompt 第13-14条）

```
锚点: normalized_question = "产科2024年11月处方合格率"
本轮: "那么外科呢"
  → 维度合并: 科室被覆盖(产科→外科) + 时间被继承(2024年11月)
  → normalized_question = "外科2024年11月处方合格率"

锚点: normalized_question = "产科2024年11月处方合格率"  
本轮: "上个月的数据"
  → 时间展开: LLM 日历推算(2024年11月→2024年10月)
  → normalized_question = "产科2024年10月处方合格率"
```

## 复合指标展开（对标海泰 MetricContext 递归结构）

```
指标: 客单价 (type=composite)
  formula = "GMV / 订单数"
  factor_metrics = ["gmv", "order_count"]
    ├─ GMV (type=single): SUM(total_amount) WHERE status IN ('paid','shipped')
    └─ 订单数 (type=single): COUNT(id)

查询"客单价" → 自动展开子指标 → 获取子指标SQL模板 → 组装完整SQL
```

## 取舍

| 决策 | 选择 | 放弃 | 理由 | 对标 |
|------|------|------|------|------|
| 向量数据库 | **Milvus** | Milvus | 专业向量检索+内置Embedding Function+HNSW索引 | 海泰同款 |
| Agent 框架 | 自建封装 | deepagents | 可控性 | 取海泰思路,不依赖三方 |
| 元数据库 | PostgreSQL | MySQL+PG | Checkpointer+Store | 海泰 PG 双角色 |
| 密钥管理 | 环境变量+启动检测 | KMS | 运维成本 | Claude Code Trust 时序 |
| 图表 | LLM 声明式 ECharts | 规则推断 | 可交互+可修改+JSON自愈 | 海泰 visualization_agent |
| 反馈回流 | 审核后回流 | 即时回流 | 防污染 | — |
| 意图识别 | normalized_question 剥离 | 直接传原文 | 向量检索去噪音 | 海泰意图识别第10条 |
| 检索失败 | 宁缺毋滥返回空 | fallback 前5张表 | 错误数据比无数据危险 | 海泰 metric_selection |
| 子Agent通信 | Store 读写 | 直接函数调用 | 解耦+可恢复+可调试 | 海泰 PostgresStore |
| SQL执行 | 单次+自愈2轮 | 反复重试 | 节约数据库资源 | 海泰 execute_sql |
| Skills | SKILL.md + reference | 单个 MD | 通用规则+类型专属 | 海泰 chart-describe 分层 |

## 风险缓解

| 风险 | 缓解 | 对标 |
|------|------|------|
| Milvus 精度不够 | LLM 二次精筛 + 宁缺毋滥策略 + 相似度过滤(0.5) | 海泰两阶段检索 |
| Agent 反思 token 消耗大 | 熔断器(3次连续失败) + 2轮自愈上限 | Claude Code autoCompact 熔断器 |
| 知识图谱推断有误 | 人工审核 + confidence 标注(0-1) | Claude Code Memory 治理 |
| 压缩后丢关键信息 | 状态补偿(语义层+SQL+筛选+Skills) | Claude Code postCompact attachments |
| LLM 输出 JSON 不完整 | 统计左右括号差异 → 补全 → 重新解析 | 海泰 ECharts JSON 自愈 |
| 数据源方言差异 | 方言从 DataSource 动态获取 + Skills reference 按方言分 | 海泰 Oracle 方言约束 |
| 追问丢失上下文 | 维度继承(cover+inherit) + anchor_question 持久化 | 海泰第13-14条 + intent_history |
| 复杂指标 SQL 过长 | 复合指标递归展开 + 子指标 SQL 模板化 | 海泰 build_metric_context |