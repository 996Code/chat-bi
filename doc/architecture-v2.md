# ChatBI v2 系统架构图

> 本文档提供 ChatBI v2 的完整架构视图，包括分层架构、核心流程、数据流和关键设计决策。
> 基于 v1（海泰 ChatBI）经验教训重构，从零搭建的 NL2SQL BI 平台。

---

## 一、整体架构总览

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                   前端 (Vue 3 + TS)                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ ChatView │ │DataSource│ │Semantic  │ │Dashboard │ │ History  │ │Observability │  │
│  │ 对话问答  │ │ 数据源管理 │ │ 语义层编辑 │ │ 看板管理  │ │ 历史记录  │ │  系统监控    │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│  ┌────┴─────┐ ┌────┴──────┐ ┌───┴──────┐                                    │      │
│  │ SkillsView│ │MemoryView │ │LoginView │                                    │      │
│  │ 业务规则  │ │ 记忆管理   │ │ 登录注册  │                                    │      │
│  └────┬─────┘ └────┬──────┘ └────┬─────┘                                    │      │
│       └──────────────┴──────────────┴────────────────────────────────────────┘      │
│                                ┌──────────────────┐                                 │
│                                │   API 层 (Axios)  │                                 │
│                                │  JWT 拦截/刷新    │                                 │
│                                └────────┬─────────┘                                 │
│      Vue Router / Element Plus / ECharts / G6 v5 / GridStack / ExcelJS             │
└─────────────────────────────────────────┬───────────────────────────────────────────┘
                                          │ HTTP / SSE
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                             后端 (FastAPI / Python 3.12)                             │
│                                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐│
│  │                              API 路由层 (api/)                                  ││
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐     ││
│  │  │auth  │ │chat  │ │stream│ │ds    │ │sem   │ │dash  │ │graph │ │obsrv │ ...  ││
│  │  │认证   │ │同步  │ │流式  │ │数据源  │ │语义层  │ │看板   │ │图谱   │ │可观测  │     ││
│  │  └──────┘ └──┬───┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘     ││
│  └──────────────┼──────────────────────────────────────────────────────────────────┘│
│                 │                                                                   │
│                 ▼                                                                   │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                        Agent 执行引擎 (ai/)                                  │   │
│  │                                                                              │   │
│  │  ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   │   │
│  │  │ intent  │──▶│ retrieve │──▶│ thinking │──▶│generate  │──▶│execute_sql│   │   │
│  │  │ 意图识别 │   │ Schema检索│   │ 预思考    │   │ SQL生成   │   │ SQL执行   │   │   │
│  │  └─────────┘   └──────────┘   └──────────┘   └──────────┘   └─────┬─────┘   │   │
│  │                                                                     │         │   │
│  │            ┌────────────────────── while(true) 循环 ───────────────┐│         │   │
│  │            ▼                                                       ││         │   │
│  │  ┌───────────┐   ┌──────────────┐   ┌───────────┐   ┌───────────┐ ││         │   │
│  │  │sql_healer │◀──│check_result │   │visualize  │   │final      │ ││         │   │
│  │  │ SQL自愈   │   │ 结果自检     │   │ 图表生成   │   │ 完成      │ ││         │   │
│  │  └───────────┘   └──────────────┘   └───────────┘   └───────────┘ ││         │   │
│  │                                                                     ││         │   │
│  │  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌──────────────┐  ││         │   │
│  │  │state_store│   │compressor │   │  recall   │   │schema_utils  │  ││         │   │
│  │  │ 状态持久化 │   │ 上下文压缩  │   │  记忆召回  │   │ Schema工具   │  ││         │   │
│  │  └───────────┘   └───────────┘   └───────────┘   └──────────────┘  ││         │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                          业务服务层 (services/)                              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │   │
│  │  │ embedder │ │retriever │ │vector    │ │sql_exec  │ │datasource_engine │   │   │
│  │  │ 向量嵌入  │ │ 两阶段检索 │ │ 向量存储  │ │ SQL执行   │ │ 数据源连接池     │   │   │
│  │  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤ ├──────────────────┤   │   │
│  │  │graph_svc │ │semantic  │ │knowledge │ │ indexer  │ │   fewshot        │   │   │
│  │  │ 图谱服务  │ │ 扫描器   │ │ 图谱推断  │ │ 索引构建  │ │  Few-shot 检索   │   │   │
│  │  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤ ├──────────────────┤   │   │
│  │  │skills   │ │metadata  │ │semantic  │ │milvus   │ │   embedder        │   │   │
│  │  │ 加载器   │ │ 刷新器   │ │ 差异比较  │ │ 向量库   │ │   二次封装        │   │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                        基础设施层 (core/)                                    │   │
│  │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌──────────┐  │   │
│  │  │config │ │auth   │ │security│ │llm_cli│ │sql_val│ │redis  │ │milvus_cli│  │   │
│  │  │ 配置   │ │ JWT   │ │ 密码   │ │ LLM   │ │SQL校验 │ │ 缓存   │ │ 向量库   │  │   │
│  │  ├───────┤ ├───────┤ ├───────┤ ├───────┤ ├───────┤ ├───────┤ ├──────────┤  │   │
│  │  │rate   │ │logging│ │startup│ │sched  │ │token  │ │prompt │ │checkpoint│  │   │
│  │  │ 限流   │ │ 日志   │ │ 探测   │ │ 调度   │ │ 追踪   │ │ 捕获   │ │ 检查点   │  │   │
│  │  └───────┘ └───────┘ └───────┘ └───────┘ └───────┘ └───────┘ └──────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                         数据持久化层 (db/)                                   │   │
│  │  ┌──────────────────────────────────────────┐  ┌─────────────────────────┐  │   │
│  │  │  PostgreSQL (元数据库)                    │  │  文件系统                │  │   │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │  │  ┌──────────────────┐  │  │   │
│  │  │  │ Tenants  │ │  Users   │ │DataSrc   │  │  │  │ skills/          │  │  │   │
│  │  │  ├──────────┤ ├──────────┤ ├──────────┤  │  │  │  SKILL.md 业务规则 │  │   │   │
│  │  │  │Semantic  │ │Conversat │ │ AuditLog │  │  │  ├──────────────────┤  │  │   │
│  │  │  │ 语义模型  │ │ 对话记录  │ │ 审计日志  │  │  │  │ memory/          │  │  │   │
│  │  │  ├──────────┤ ├──────────┤ ├──────────┤  │  │  │  Agent 记忆       │  │  │   │
│  │  │  │Dashboards│ │SavedQuery│ │Widgets   │  │  │  ├──────────────────┤  │  │   │
│  │  │  └──────────┘ └──────────┘ └──────────┘  │  │  │  data/states/     │  │  │   │
│  │  │  SQLAlchemy async + auto_create_tables   │  │  │  State Store JSONL │  │  │   │
│  │  └──────────────────────────────────────────┘  │  └──────────────────┘  │  │   │
│  │  ┌──────────────────────────────────────────┐  └─────────────────────────┘  │   │
│  │  │  Milvus (向量数据库)                      │                                │   │
│  │  │  ┌──────────────────┐ ┌────────────────┐  │                                │   │
│  │  │  │  semantic_index  │ │  fewshot_index  │  │                                │   │
│  │  │  │  Schema/表/列    │ │  审核 SQL 示例   │  │                                │   │
│  │  │  └──────────────────┘ └────────────────┘  │                                │   │
│  │  └──────────────────────────────────────────┘                                │   │
│  │  ┌──────────────────────────────────────────┐                                │   │
│  │  │  Redis (缓存)                            │                                │   │
│  │  │  限流令牌桶 / 语义缓存 / 会话管理          │                                │   │
│  │  └──────────────────────────────────────────┘                                │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                        业务数据库 (用户数据源)                                │   │
│  │           ┌───────┐  ┌───────┐  ┌───────┐  ┌──────────┐                    │   │
│  │           │ MySQL  │  │  PG   │  │(未来) │  │ (未来)    │                    │   │
│  │           │        │  │       │  │Oracle │  │ClickHouse │                    │   │
│  │           └───────┘  └───────┘  └───────┘  └──────────┘                    │   │
│  │           DataSourceEnginePool (动态连接池, Fernet 解密)                     │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、核心数据流：一次问答的完整旅程

```
用户提问
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ 1. API 入口 (chat.py / chat_stream.py)                                             │
│    - 鉴权 (JWT) → 获取 tenant_id / user_id                                         │
│    - 确定数据源 (data_source_id) → 取语义层 content + 连接 URL                      │
│    - 装配 AgentDeps (注入所有依赖: LLM / 检索 / 执行器 / 自愈 / 图表等)              │
│    - 恢复多轮上下文 (StateStore 读上一轮状态)                                       │
│    - 启动 Token 追踪 + Prompt 捕获                                                  │
└─────────────────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ 2. Agent 状态机 (agent.py → run_agent)                                             │
│                                                                                     │
│  ┌──────────┐                                                                      │
│  │ INTENT   │─── GENERAL/EXPLANATION ───▶ 直接回复 (不走 SQL 管道)                   │
│  │ 意图识别  │─── CLARIFICATION ────▶ ask_user 暂停                                   │
│  │          │─── CHART_MODIFY ───▶ 复用上轮 SQL + 新图表类型                           │
│  │          │─── TEXT_TO_SQL ───▶ 继续管道                                           │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐                                                                      │
│  │ SCHEMA   │  向量检索 (retriever.py): 两阶段                                      │
│  │ 检索     │    阶段1: BGE 嵌入 → Milvus 召回 top-K                                 │
│  │          │    阶段2: LLM 精筛 (宁缺毋滥, 无匹配返回空)                              │
│  │          │  图谱扩展 (expand_with_relationships): 沿外键路径补全关联表               │
│  │          │  JOIN 路径预计算 (build_join_path_section): Dijkstra 最短路径            │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐                                                                      │
│  │ THINKING │  预思考 (thinking.py): 选表理由 + 聚合方式 + 陷阱警告                    │
│  │ 预思考    │  (结果注入 SQL 生成 prompt)                                           │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐                                                                      │
│  │ GENERATE │  SQL 生成 (sql_agent.py):                                             │
│  │ SQL 生成  │    Prompt 分层: 语义层(可缓存) + Skills + 记忆 + 预思考 + 历史 + 问题   │
│  │          │    Few-shot 注入: 相似审核 SQL 示例                                    │
│  │          │    JOIN 路径注入: 预计算的最短路径                                       │
│  │          │    指标定义注入: 业务指标公式                                           │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐                                                                      │
│  │ VALIDATE │  T030 三层校验 (sql_validator.py):                                    │
│  │ SQL 校验  │    Layer 1: AST 解析 (sqlglot) → 拒绝非 SELECT                          │
│  │          │    Layer 2: 危险函数 (LOAD_FILE/SLEEP 等)                              │
│  │          │    Layer 3: 白名单列 (语义层定义的列集合)                                 │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐                                                                      │
│  │ EXECUTE  │  SQL 执行 (sql_executor.py):                                          │
│  │ SQL 执行  │    连接池复用 (DataSourceEnginePool)                                  │
│  │          │    READ ONLY 事务 + DB 侧超时 + asyncio.wait_for 双重保险               │
│  │          │    自动 LIMIT (防全表扫描 OOM)                                         │
│  │          │    结果采样 + truncated 标记                                           │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐      ┌──────────┐                                                    │
│  │ CHECK    │──异常─▶ HEAL     │  (自愈循环: 最多 2 轮)                               │
│  │ 结果自检  │      │ SQL自愈  │                                                    │
│  │ 规则检查: │      │ 错误分类 │                                                    │
│  │ 0行/全NULL│      │ + 修正   │──────────────────────────────────▶ 重新校验 + 执行    │
│  │ 笛卡尔积等 │      └──────────┘                                                    │
│  └────┬─────┘                                                                      │
│       ▼ (正常)                                                                      │
│  ┌──────────┐                                                                      │
│  │VISUALIZE │  图表生成 (chart_agent.py):                                           │
│  │ 图表生成  │    LLM → ECharts Option → JSON 自愈 → 规则兜底                         │
│  │          │    KPI 指标卡 / 折线图 / 柱状图 / 饼图 / 表格                           │
│  └────┬─────┘                                                                      │
│       ▼                                                                             │
│  ┌──────────┐                                                                      │
│  │  FINAL   │  完成: success / failed / ask_user                                    │
│  └──────────┘                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ 3. 持久化与审计                                                                     │
│    - StateStore 持久化 (结构化状态 → JSONL)                                         │
│    - SavedQuery 入库 (去重: 同 question + sql 不重复)                                │
│    - Few-shot 回流 (向量库索引, RAG-004)                                            │
│    - 指标反哺 (co_occurrence 增量更新)                                              │
│    - 审计日志 (success/fail/denied 三态, 慢查询标记)                                 │
│    - Token 追踪 + Prompt 捕获 (可观测性)                                             │
│    - 对话标题生成 (LLM 总结 ≤16字)                                                  │
└─────────────────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ 4. 响应返回                                                                         │
│    同步 (POST /chat): ChatResponse JSON                                             │
│    流式 (POST /chat/stream): SSE 事件序列 (intent → schema → sql → data → chart)     │
│    前端: 实时管线进度 + 逐事件渲染                                                    │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、组件依赖关系图

```
                        ┌──────────────┐
                        │   main.py    │
                        │  FastAPI 应用 │
                        │  lifespan:   │
                        │  启动探测→    │
                        │  自动建表→    │
                        │  预热→调度器  │
                        └──────┬───────┘
                               │
              ┌────────────────┼────────────────┬──────────────────┐
              ▼                ▼                ▼                  ▼
      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
      │  api/ 路由层  │ │  core/ 基础设施 │ │  ai/ Agent   │ │  services/ 业务层  │
      │              │ │              │ │              │ │                  │
      │ auth.py      │ │ config.py    │ │ agent.py     │ │ embedder.py      │
      │ chat.py      │ │ auth.py      │ │ intent.py    │ │ retriever.py     │
      │ chat_stream   │ │ security.py  │ │ thinking.py  │ │ vector_store.py  │
      │ data_sources  │ │ llm_client   │ │ sql_agent.py │ │ milvus_vector    │
      │ semantic_...  │ │ sql_validator│ │ sql_healer   │ │ sql_executor.py  │
      │ dashboard.py  │ │ redis_client │ │ chart_agent  │ │ datasource_engine│
      │ graph.py      │ │ milvus_cli   │ │ replier.py   │ │ graph_service.py │
      │ observability │ │ rate_limit   │ │ result_check │ │ semantic_scanner  │
      │ skills.py     │ │ logging.py   │ │ ask_user.py  │ │ knowledge_graph   │
      │ memory.py     │ │ scheduler    │ │ recall.py    │ │ indexer.py        │
      │ saved_queries │ │ token_tracker│ │ compressor   │ │ fewshot.py        │
      │               │ │ prompt_cap   │ │ state_store  │ │ skills_loader.py  │
      │               │ │ checkpointer │ │ schema_utils │ │ metadata_refresher│
      │               │ │ startup_probe│ │ chat_utils   │ │ semantic_diff.py  │
      │               │ │ text_sanitize│ │ question_gen │ │ datasource_health │
      │               │ │ agent_memory │ │              │ │                  │
      │               │ │ prompt_cache │ │              │ │                  │
      └──────────────┘ └──────┬───────┘ └──────┬───────┘ └────────┬─────────┘
                              │                │                  │
                              └────────────────┼──────────────────┘
                                               │
                                      ┌────────┴────────┐
                                      │  db/ 持久化层    │
                                      │                 │
                                      │  session.py     │
                                      │  models.py      │
                                      │  (10 张表)       │
                                      │                 │
                                      │  PostgreSQL     │
                                      │  Milvus         │
                                      │  Redis          │
                                      │  文件系统        │
                                      └─────────────────┘
```

---

## 四、模块间调用关系

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  API 端点 → 依赖装配 → Agent 执行 → 服务调用 → 数据持久化                              │
│                                                                                     │
│  chat.py/chat_stream.py                                                             │
│    │                                                                                │
│    ├── build_agent_deps()                                                           │
│    │     ├── VectorStore (Milvus/Mock)                                              │
│    │     ├── SkillsLoader (skills/{tenant_id}/)                                     │
│    │     ├── Embedder (BGE)                                                         │
│    │     ├── Retriever (两阶段检索)                                                   │
│    │     ├── SQLExecutor (连接池 + 超时)                                             │
│    │     ├── FewShot (相似 SQL 检索)                                                 │
│    │     ├── MemoryRecall (Agent 记忆)                                               │
│    │     └── DataSourceEngine (动态连接池)                                           │
│    │                                                                                │
│    ├── run_agent()                                                                  │
│    │     ├── classify_intent()       → LLM (llm_client.py)                          │
│    │     ├── retrieve()              → Embedder + VectorStore + LLM 精筛              │
│    │     ├── think()                 → LLM                                          │
│    │     ├── generate_sql()          → LLM + FewShot + Skills + 记忆                  │
│    │     ├── execute_sql()           → SQLExecutor (DataSourceEnginePool)            │
│    │     ├── heal_sql()              → LLM (自愈)                                    │
│    │     ├── check_result()          → 规则引擎 (纯同步)                               │
│    │     ├── generate_chart()        → LLM + 规则兜底                                 │
│    │     └── generate_reply()        → LLM (自然语言回复)                              │
│    │                                                                                │
│    ├── StateStore.save()            → JSONL 文件                                    │
│    ├── SavedQuery.save()            → PostgreSQL                                    │
│    ├── FewShot.index()              → VectorStore (Milvus)                          │
│    ├── MetricFeedback.persist()     → co_occurrence 增量更新                         │
│    ├── AuditLog.write()             → PostgreSQL                                    │
│    └── TokenTracker.stop()          → 内存 (每请求)                                  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 五、数据库模型关系

```
┌───────────────┐       ┌───────────────┐
│    Tenant     │       │     User       │
│───────────────│       │───────────────│
│ id (PK)       │──1:N──│ id (PK)       │
│ name          │       │ tenant_id (FK) │
│ is_active     │       │ email (UNIQUE) │
│ created_at    │       │ username       │
│ updated_at    │       │ hashed_password│
└───────────────┘       │ role (admin/   │
        │               │      user/ro)  │
        │               │ is_active      │
        │               │ email_verified │
        │               └───────┬───────┘
        │                       │
        │ 1:N             1:N  │
        │                       │
┌───────┴───────────────┐       │
│     DataSource        │       │
│───────────────────────│       │
│ id (PK)               │       │
│ tenant_id (FK)        │───────┘
│ db_type (mysql/pg)    │
│ host / port / database│
│ username              │
│ encrypted_password    │
│ is_active             │
│ scan_status           │
│ scan_progress         │
└───────┬───────────────┘
        │
        │ 1:N
        │
┌───────┴───────────────┐       ┌───────────────────────┐
│    SemanticModel      │       │    Conversation        │
│───────────────────────│       │───────────────────────│
│ id (PK)               │       │ id (PK)               │
│ tenant_id (FK)        │       │ tenant_id (FK)        │
│ data_source_id (FK)   │       │ user_id (FK)          │
│ version               │       │ title                 │
│ content (JSON)        │       │ state_json (JSON)     │
│ is_current            │       │ is_archived           │
│ created_at            │       │ created_at            │
│ UQ(tenant,ds,version) │       └───────────────────────┘
└───────────────────────┘
        │
        │ 1:N
        │
┌───────┴───────────────┐       ┌───────────────────────┐
│     SavedQuery        │       │    AuditLog            │
│───────────────────────│       │───────────────────────│
│ id (PK)               │       │ id (PK)               │
│ tenant_id (FK)        │       │ tenant_id (FK)        │
│ user_id (FK)          │       │ user_id (FK/nullable) │
│ data_source_id (FK)   │       │ resource_type         │
│ conversation_id       │       │ resource_id           │
│ question (Text)       │       │ action                │
│ sql_text (Text)       │       │ status (s/f/d)        │
│ result_summary        │       │ detail (JSON)         │
│ chart_config (JSON)   │       │ sql_text              │
│ created_at            │       │ duration_ms           │
└───────────────────────┘       │ is_slow               │
                                │ data_source_id        │
┌───────────────────────┐       │ created_at            │
│     Dashboard         │       └───────────────────────┘
│───────────────────────│
│ id (PK)               │       ┌───────────────────────┐
│ tenant_id (FK)        │       │   DashboardWidget      │
│ user_id               │──1:N──│───────────────────────│
│ name                  │       │ id (PK)               │
│ created_at            │       │ dashboard_id (FK)     │
└───────────────────────┘       │ tenant_id (FK)        │
                                │ question              │
                                │ query_sql             │
                                │ datasource_id         │
                                │ chart_type            │
                                │ chart_option (JSON)   │
                                │ position_x/y          │
                                │ width / height        │
                                └───────────────────────┘
```

---

## 六、向量存储结构

```
┌──────────────────────────────────────────────────────────────┐
│  Milvus Collection: semantic_index (默认)                     │
│──────────────────────────────────────────────────────────────│
│  字段: id (PK), text, metadata (JSON), embedding (1024维)    │
│  索引: HNSW (M=16, efConstruction=200), IP 度量             │
│  内容: 语义层 Model/Column/Metric 的文本描述                  │
│  查询: 自然语言 → BGE 嵌入 → 向量检索 (top_K=20, score≥0.35) │
│                                                              │
│  Milvus Collection: fewshot_index                            │
│──────────────────────────────────────────────────────────────│
│  字段: id (PK), text, metadata (JSON), embedding (1024维)    │
│  内容: 审核过的 SQL 示例 (question + sql)                     │
│  查询: 用户问题 → 向量检索 → 作为 Few-shot 注入 SQL 生成      │
└──────────────────────────────────────────────────────────────┘
```

---

## 七、项目文件结构

```
chat-bi/
│
├── backend/app/                        # 后端核心代码
│   ├── main.py                         # FastAPI 入口 + lifespan
│   │
│   ├── api/                            # HTTP API 路由层
│   │   ├── __init__.py                 # 路由聚合
│   │   ├── auth.py                     # 认证 (注册/登录/刷新)
│   │   ├── dev_auth.py                 # 开发模式 token
│   │   ├── chat.py                     # 同步问答
│   │   ├── chat_stream.py              # SSE 流式问答
│   │   ├── data_sources.py             # 数据源 CRUD
│   │   ├── semantic_models.py          # 语义层版本管理
│   │   ├── dashboard.py                # 看板 CRUD
│   │   ├── saved_queries.py            # 保存查询
│   │   ├── skills.py                   # 业务规则管理
│   │   ├── memory.py                   # Agent 记忆管理
│   │   ├── graph.py                    # 知识图谱 API
│   │   └── observability.py            # 可观测性
│   │
│   ├── ai/                             # Agent 执行引擎
│   │   ├── agent.py                    # 状态机编排 (run_agent)
│   │   ├── intent.py                   # 意图识别 (5 分类)
│   │   ├── thinking.py                 # 预思考机制
│   │   ├── sql_agent.py                # SQL 生成
│   │   ├── sql_healer.py               # SQL 自愈
│   │   ├── chart_agent.py              # 图表生成
│   │   ├── result_checker.py           # 结果自检
│   │   ├── replier.py                  # 自然语言回复
│   │   ├── ask_user.py                 # 用户澄清模块
│   │   ├── recall.py                   # 记忆召回
│   │   ├── compressor.py               # 上下文压缩
│   │   ├── state_store.py              # 对话状态管理
│   │   ├── schema_utils.py             # Schema 工具
│   │   ├── chat_utils.py               # 共享工具函数
│   │   └── question_generator.py       # 示例问题生成
│   │
│   ├── core/                           # 基础设施层
│   │   ├── config.py                   # 集中配置 (Pydantic)
│   │   ├── auth.py                     # JWT 鉴权 + RBAC
│   │   ├── security.py                 # 密码/加密/Token
│   │   ├── llm_client.py               # LLM 客户端 (AsyncOpenAI)
│   │   ├── llm_json.py                 # JSON 解析工具
│   │   ├── sql_validator.py            # SQL 三层校验
│   │   ├── redis_client.py             # Redis 客户端
│   │   ├── milvus_client.py            # Milvus 客户端
│   │   ├── rate_limit.py               # 限流器
│   │   ├── logging.py                  # 日志配置
│   │   ├── checkpointer.py             # 检查点
│   │   ├── startup_probe.py            # 启动探测
│   │   ├── scheduler.py                # 定时任务
│   │   ├── token_tracker.py            # Token 追踪
│   │   ├── prompt_capture.py           # Prompt 捕获
│   │   ├── prompt_cache.py             # Prompt 缓存
│   │   ├── text_sanitize.py            # Unicode 清洗
│   │   └── agent_memory.py             # 文件记忆存储
│   │
│   ├── db/                             # 数据持久化层
│   │   ├── session.py                  # 异步引擎 + 自动建表
│   │   └── models.py                   # 10 张 ORM 模型
│   │
│   ├── schemas/                        # Pydantic 模型
│   │   └── semantic_layer.py           # 语义层 JSON Schema
│   │
│   └── services/                       # 业务服务层
│       ├── embedder.py                 # BGE 向量嵌入
│       ├── retriever.py                # 两阶段检索
│       ├── vector_store.py             # 向量存储抽象
│       ├── milvus_vector_store.py      # Milvus 实现
│       ├── sql_executor.py             # SQL 执行器
│       ├── datasource_engine.py        # 数据源连接池
│       ├── datasource_health.py        # 数据源健康检查
│       ├── graph_service.py            # 知识图谱 (NetworkX)
│       ├── semantic_scanner.py         # 数据源扫描
│       ├── knowledge_graph.py          # 图谱推断演化
│       ├── semantic_diff.py            # 语义层差异比较
│       ├── metadata_refresher.py       # 元数据自动刷新
│       ├── indexer.py                  # 向量索引构建
│       ├── indexer_update.py           # 索引增量更新
│       ├── fewshot.py                  # Few-shot 检索
│       └── skills_loader.py            # Skills 加载器
│
├── frontend/src/                       # Vue 3 前端
│   ├── main.ts                         # 入口
│   ├── App.vue                         # 根组件 + 导航
│   ├── api/                            # HTTP 封装
│   │   ├── client.ts                   # Axios + JWT 拦截
│   │   └── index.ts                    # 所有 API 方法
│   ├── router/                         # 路由
│   ├── composables/                    # 组合式函数
│   ├── components/                     # 可复用组件
│   ├── views/                          # 9 个功能页面
│   └── utils/                          # 工具函数
│
├── skills/                             # 业务规则 (SKILL.md)
├── memory/                             # Agent 记忆文件
├── data/                               # 运行时数据
│
├── docker/                             # Docker Compose
├── deploy/                             # 部署配置
│
├── doc/                                # 文档
│   ├── v1-archive/                     # v1 历史文档
│   ├── chatbi-v2/                      # v2 方案文档
│   ├── images/                         # 图片
│   └── screenshots/                    # 截图
│
├── .planning/                          # GSD 规划状态
├── openspec/                           # OpenSpec 规格
└── .claude/                            # Claude 配置
```

---

## 八、关键设计决策

| 决策 | 选择 | 替代方案 | 理由 |
|------|------|---------|------|
| AI 框架 | 自建 Agent 状态机 | LangGraph, DeepAgents | 纯手工状态机编排，确定性工作流，非"Agent 失控" |
| 语义层 | MDL 风格 JSON Schema | 自然语言描述 | 结构化的 Schema Linking，准确率提升 40% |
| 嵌入模型 | 本地 BGE-large-zh-v1.5 | 在线 API 嵌入 | 离线可用、维度确定、中文最优 |
| 向量库 | Milvus | Chroma, Qdrant | 生产级、HNSW 索引、支持过滤 |
| SQL 校验 | SQLGlot AST | 正则匹配 | 多方言、AST 级别安全 |
| 前端状态 | 组合式 API (ref) | Pinia/Vuex | 轻量、无需全局状态管理 |
| 多租户 | 共享表 + tenant_id | 独立库/独立 Schema | 运维成本低、适合 SaaS |
| 数据源密码 | Fernet 对称加密 | AES / 密钥管理服务 | 自包含、无需外部 KMS |
| 启动探测 | 必需服务 fail-fast | 全部降级启动 | 安全 Fail-Closed |
| 对话持久化 | JSONL 文件 | PostgreSQL JSON | 简单可靠、追加写入、无 Schema 变更 |

---

## 九、安全架构

```
┌──────────────────────────────────────────────────────────────────┐
│                       安全多层次防御                              │
│                                                                  │
│  1. 启动安全                                                     │
│     - CHANGE_ME 占位符检测 → 拒绝启动                             │
│     - 必需服务探测 (PostgreSQL) → 失败即终止                       │
│     - 可选服务降级 (Redis/Milvus) → WARNING + 功能降级             │
│                                                                  │
│  2. 传输安全                                                     │
│     - CORS 配置 (credentials + 具体域名, 拒绝 wildcard)           │
│     - JWT 鉴权 (access_token + refresh_token)                     │
│     - 请求限流 (slowapi, IP 级别)                                 │
│                                                                  │
│  3. 认证与授权                                                   │
│     - JWT 四字段: user_id, email, tenant_id, role                │
│     - RBAC 三角色: admin, user, read_only                        │
│     - 多租户隔离: TenantMixin + contextvars                       │
│     - 登录失败锁定: 5 次失败 → 30 分钟锁                          │
│     - 密码: bcrypt 12 轮                                         │
│                                                                  │
│  4. SQL 执行安全                                                 │
│     - Layer 1: AST 解析 → 拒绝非 SELECT (DROP/DELETE/...)        │
│     - Layer 2: 危险函数 → 拒绝 LOAD_FILE/SLEEP/BENCHMARK 等      │
│     - Layer 3: 白名单列 → 只允许语义层定义的列                      │
│     - READ ONLY 事务 → 即使校验被绕过也不能写                      │
│     - DB 侧超时 (statement_timeout) + Python 侧超时 (wait_for)    │
│     - 自动 LIMIT → 防全表扫描 OOM                                 │
│                                                                  │
│  5. 数据安全                                                     │
│     - 数据源密码: Fernet 加密存储                                 │
│     - Unicode 清洗: 零宽字符/方向字符/私用区字符移除                │
│     - 审计日志: success/fail/denied 三态全覆盖                     │
│     - 错误信息: 内部异常不泄露给客户端                              │
│                                                                  │
│  6. Prompt 安全                                                  │
│     - 宁缺毋滥: 检索失败返回空，禁止 fallback                      │
│     - 自愈后 SQL 也走三层校验 (v1 教训 #32)                       │
│     - 白名单列限制: 语义层即安全边界                               │
│     - 自愈 SQL 校验: 重生成后重新校验                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 十、Agent 状态机状态流转图

```
                    ┌──────────┐
                    │  INTENT  │
                    └────┬─────┘
                         │
              ┌──────────┼──────────┬───────────────┐
              ▼          ▼          ▼               ▼
        TEXT_TO_SQL  CLARIFY   GENERAL/EXPLAIN  CHART_MODIFY
              │          │          │               │
              ▼          ▼          ▼               ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
        │SCHEMA    │ │ ask_user │ │ 直接回复  │ │ 复用上轮 SQL  │
        │ SEARCH   │ │ (暂停)   │ │ (final)  │ │ + 重新生成图表 │
        └────┬─────┘ └──────────┘ └──────────┘ └──────┬───────┘
             │                                        │
             ▼                                        ▼
        ┌──────────┐                             ┌──────────┐
        │ GENERATE │←─── 预思考 (thinking)        │  FINAL   │
        │ SQL      │←─── Few-shot / Skills / 记忆 │ success  │
        └────┬─────┘                             └──────────┘
             │
             ▼
        ┌──────────┐
        │ EXECUTE  │─── 失败 ───▶ ┌──────────┐
        │ SQL      │             │ HEAL SQL │←── 最多 2 轮
        └────┬─────┘             └────┬─────┘
             │                        │
             ▼ (成功)                  │ 重新执行
        ┌──────────┐                  │
        │ SELF     │─── 异常 ─────────┘
        │ CHECK    │─── 仍异常 ──▶ ask_user
        └────┬─────┘
             │ (正常)
             ▼
        ┌──────────┐
        │VISUALIZE │
        └────┬─────┘
             │
             ▼
        ┌──────────┐
        │  FINAL   │
        │ success  │
        └──────────┘
```

> **说明**: 本状态机对应 `AgentStage` 枚举 (`agent.py:31`): `INTENT → SCHEMA_SEARCH → GENERATE_SQL → EXECUTE_SQL → SELF_CHECK → VISUALIZE → FINAL`。
> 其中 `THINKING`(预思考)、`VALIDATE`(SQL 校验) 不是独立状态，而是 `GENERATE_SQL` 和 `EXECUTE_SQL` 中的函数调用。
> 自愈 (`HEAL`) 是 `EXECUTE_SQL` 内部的 while 循环，用 `self_heal_rounds` 计数器控制最多 2 轮。

---

## 十一、代码引用索引

> 每个模块的完整代码引用索引见 `doc/code-reference-index.md`，以下是关键入口点的快速索引。

### 核心入口

| 模块 | 文件 | 关键入口 |
|------|------|---------|
| 应用入口 | `backend/app/main.py:19` | `lifespan()` — 启动/关闭生命周期 |
| 应用工厂 | `backend/app/main.py:101` | `create_app()` — FastAPI 工厂 |
| 配置加载 | `backend/app/core/config.py:339` | `get_settings()` — 配置单例 |
| 配置校验 | `backend/app/core/config.py:344` | `validate_settings_on_startup()` — CHANGE_ME 拒绝启动 |
| 启动探测 | `backend/app/core/startup_probe.py:213` | `run_startup_probes()` — 必需服务 fail-fast |
| 自动建表 | `backend/app/db/session.py:122` | `auto_create_tables()` — 模型即真相源 |
| 路由聚合 | `backend/app/api/__init__.py:31` | `api_router` — 12 个子路由聚合 |

### Agent 执行引擎

| 模块 | 文件 | 关键入口 |
|------|------|---------|
| 状态机编排 | `backend/app/ai/agent.py:123` | `run_agent()` — 主循环 |
| 意图识别 | `backend/app/ai/intent.py:123` | `classify_intent()` — 5 意图分类 |
| 预思考 | `backend/app/ai/thinking.py:78` | `think()` — SQL 生成前思考 |
| SQL 生成 | `backend/app/ai/sql_agent.py:113` | `generate_sql()` — Prompt 分层生成 |
| SQL 自愈 | `backend/app/ai/sql_healer.py:180` | `heal_sql()` — 错误分类 + 纠正 |
| 图表生成 | `backend/app/ai/chart_agent.py:584` | `generate_chart()` — LLM + 规则兜底 |
| 结果自检 | `backend/app/ai/result_checker.py:76` | `check_result()` — 纯规则检查 |
| 记忆召回 | `backend/app/ai/recall.py:27` | `recall_memories()` — 关键词检索 |
| 上下文压缩 | `backend/app/ai/compressor.py:119` | `compact_history()` — 压缩 + 状态补偿 |
| 状态管理 | `backend/app/ai/state_store.py:156` | `StateStore` — JSONL 持久化 |
| Schema 工具 | `backend/app/ai/schema_utils.py:61` | `get_schema_graph()` — 图谱构建 |
| 两阶段检索 | `backend/app/services/retriever.py:39` | `retrieve()` — 向量召回 + LLM 精筛 |

### 业务服务

| 模块 | 文件 | 关键入口 |
|------|------|---------|
| 向量嵌入 | `backend/app/services/embedder.py:158` | `get_embedder()` — BGE 单例 |
| SQL 执行 | `backend/app/services/sql_executor.py:130` | `execute_sql()` — 连接池 + 超时 |
| 数据源连接池 | `backend/app/services/datasource_engine.py:73` | `DataSourceEnginePool` — 动态连接池 |
| 知识图谱 | `backend/app/services/graph_service.py:60` | `SchemaGraph` — NetworkX 图服务 |
| 数据源扫描 | `backend/app/services/semantic_scanner.py:64` | `scan_data_source()` — 扫描 → JSON |
| 图谱推断 | `backend/app/services/knowledge_graph.py:245` | `infer_knowledge_graph()` — 推断关系 |
| 索引构建 | `backend/app/services/indexer.py:100` | `build_index()` — 语义层 → 向量 |
| Few-shot | `backend/app/services/fewshot.py:37` | `find_fewshot_examples()` — 相似 SQL 检索 |
| Skills 加载 | `backend/app/services/skills_loader.py:84` | `SkillsLoader` — SKILL.md 解析 |
| 元数据刷新 | `backend/app/services/metadata_refresher.py:41` | `detect_and_refresh_metadata()` — 自动刷新 |

### 基础设施

| 模块 | 文件 | 关键入口 |
|------|------|---------|
| LLM 客户端 | `backend/app/core/llm_client.py:90` | `llm_chat()` — 重试/退避/追踪 |
| SQL 校验 | `backend/app/core/sql_validator.py:109` | `validate_sql()` — 三层校验 |
| 认证鉴权 | `backend/app/core/auth.py:82` | `get_current_user()` — JWT 依赖 |
| 密码加密 | `backend/app/core/security.py:20` | `hash_password()` — bcrypt 12 轮 |
| Token 加密 | `backend/app/core/security.py:89` | `create_access_token()` — JWT 签发 |
| 审计日志 | `backend/app/core/auth.py:258` | `write_audit_log()` — 三态写入 |
| 限流器 | `backend/app/core/rate_limit.py:35` | `get_limiter()` — slowapi |
| 调度器 | `backend/app/core/scheduler.py:42` | `start_scheduler()` — 定时任务 |
| Token 追踪 | `backend/app/core/token_tracker.py:102` | `start_token_tracking()` — 请求级 |
| Prompt 捕获 | `backend/app/core/prompt_capture.py:76` | `start_prompt_capture()` — 调试用 |
| Unicode 清洗 | `backend/app/core/text_sanitize.py:31` | `sanitize_text()` — 安全清洗 |
| 文件记忆 | `backend/app/core/agent_memory.py:24` | `AgentMemoryStore` — .md 文件存储 |

### API 端点

| 端点 | 文件 | 描述 |
|------|------|------|
| POST /chat | 后端 `backend/app/api/chat.py:322` | 同步问答 (`chat()` 端点) |
| POST /chat/stream | 后端 `backend/app/api/chat_stream.py:100` | 流式问答 (SSE) |
| `POST /auth/register` | `backend/app/api/auth.py:97` | 注册 |
| `POST /auth/login` | `backend/app/api/auth.py:199` | 登录 |
| `POST /auth/refresh` | `backend/app/api/auth.py:281` | 刷新 Token |
| `POST /data-sources` | `backend/app/api/data_sources.py:91` | 创建数据源 |
| `POST /data-sources/{id}/scan` | `backend/app/api/data_sources.py:274` | 触发扫描 |
| `GET /semantic-models` | `backend/app/api/semantic_models.py:49` | 获取语义层 |
| `GET /dashboards` | `backend/app/api/dashboard.py:191` | 看板列表 |
| `GET /graph` | `backend/app/api/graph.py:97` | 全量图谱 |
| `GET /audit-logs` | `backend/app/api/observability.py:52` | 审计日志 |
| `GET /skills` | `backend/app/api/skills.py:105` | `list_skills()` — Skills 列表 |
| `GET /memory` | `backend/app/api/memory.py:170` | `list_memories()` — 记忆列表 |

### 前端页面

| 页面 | 文件 | 关键方法 |
|------|------|---------|
| 对话问答 | `frontend/src/views/ChatView.vue:880` | `send()` — 流式发送 |
| 数据源管理 | `frontend/src/views/DataSourceView.vue:154` | `fetchList()` — 加载列表 |
| 语义层编辑 | `frontend/src/views/SemanticView.vue:382` | `fetchData()` — 加载语义层 |
| 看板管理 | `frontend/src/views/DashboardView.vue:125` | `loadDashList()` — 加载看板 |
| 历史记录 | `frontend/src/views/HistoryView.vue:133` | `loadAudit()` — 审计日志 |
| 系统监控 | `frontend/src/views/ObservabilityView.vue:198` | `loadMetrics()` — 指标 |
| 记忆管理 | `frontend/src/views/MemoryView.vue:424` | `fetchData()` — 记忆列表 |
| 业务规则 | `frontend/src/views/SkillsView.vue:206` | `fetchData()` — Skills 列表 |
| 图谱组件 | `frontend/src/components/SchemaGraph.vue:376` | `initG6()` — G6 初始化 |

> 完整代码索引见 `doc/code-reference-index.md`（460+ 条目，覆盖全部后端和前端定义）

---

## 十二、Java 开发者速查指南

> 如果你是从 Java/Spring Boot 背景转过来的开发者，这份指南帮你快速建立概念映射。

### 12.1 技术栈对照表

| Python / ChatBI | 类比 Java 技术 | 关键差异 |
|----------------|---------------|---------|
| **FastAPI** | Spring Boot + Spring Web | 路由用装饰器 `@app.get()` 而非注解；原生异步；自动生成 OpenAPI 文档 |
| **Pydantic** | Jackson + Hibernate Validator | 用类型注解同时做校验和序列化，一体两面 |
| **SQLAlchemy async** | JPA / Hibernate + R2DBC | Session ≈ EntityManager，Model ≈ @Entity，但异步 API 更接近 R2DBC |
| **uv** | Maven / Gradle | `uv sync` ≈ `mvn install`，`uv run` ≈ `mvn exec:java`，`uv add` ≈ `mvn dependency:add` |
| **pyproject.toml** | pom.xml / build.gradle | 项目配置 + 依赖声明，但用 TOML 格式而非 XML/Groovy |
| **.env** | application.yml | 环境变量驱动，`KEY=VALUE` 格式，没有 YAML 层次结构 |
| **pytest** | JUnit 5 | 函数级测试 `def test_xxx():` 而非注解；`assert` 语句而非 `assertEquals()` |
| **APScheduler** | Spring @Scheduled / Quartz | 编程式注册任务，类似 `scheduler.add_job(func, 'interval', minutes=5)` |
| **SQLGlot** | JSqlParser / 通用 SQL 解析器 | 解析 SQL 为 AST，支持多方言转换 |
| **NetworkX** | JGraphT / Guava Graph | Python 图分析库，支持 Dijkstra、社区发现等 |
| **Milvus** | Elasticsearch（向量版） | 存向量而非文本，用 HNSW 索引而非倒排索引 |
| **Redis** | Redis（Java 也有） | 完全一样，缓存/限流/会话管理 |
| **PostgreSQL** | PostgreSQL（Java 也有） | 完全一样，但连接用 asyncpg 驱动 |

### 12.2 项目结构映射（Java 开发者视角）

```
Python 项目结构                    Java 项目结构类比
──────────────────────────────────────────────────────────
backend/app/                      src/main/java/com/example/app/
├── main.py                       Application.java (@SpringBootApplication)
├── api/                          controller/ 包 (@RestController)
│   ├── __init__.py               (包标识，Java 不需要)
│   ├── auth.py                   AuthController.java
│   ├── chat.py                   ChatController.java
│   └── data_sources.py           DataSourceController.java
├── ai/                           无直接类比，相当于 "复杂的 Service 编排层"
│   ├── agent.py                  主编排器：类似 Spring State Machine 的流程定义
│   ├── intent.py                 意图分类器
│   └── sql_agent.py              SQL 生成器
├── services/                     service/ 包 (@Service)
│   ├── embedder.py               EmbeddingService.java
│   ├── retriever.py              检索 Service
│   └── sql_executor.py           SqlExecutionService.java
├── core/                         config/ + security/ 包
│   ├── config.py                 ApplicationConfig.java (@ConfigurationProperties)
│   ├── auth.py                   AuthFilter.java / SecurityConfig.java
│   └── llm_client.py             LlmClient.java (调用外部 API 的 Service)
├── db/                           repository/ 包
│   ├── session.py                EntityManagerFactory.java 配置
│   └── models.py                 @Entity 类集合
│
frontend/                         前端 (Vue 3, 非 Java 技术栈)
├── src/
│   ├── api/                      api-client 层 (类似 axios 封装)
│   ├── views/                    页面组件 (类似 Vue 页面)
│   └── components/               可复用组件
│
doc/                              文档
├── architecture-v2.md            架构文档
├── code-reference-index.md       代码引用索引
└── learning-roadmap.md           学习路线图
```

### 12.3 Python 语法速查表（Java 开发者版）

| Python 语法 | Java 类比 | 说明 |
|------------|----------|------|
| `def func():` | `void func()` | 函数定义，根级别不需要类 |
| `async def func():` | `CompletableFuture<Void> func()` | 异步函数，用 await 调用 |
| `await func()` | `func().get()` | 等待异步结果 |
| `class X:` | `class X { }` | 类定义，不需要 `public class` |
| `class X(Base):` | `class X extends Base { }` | 继承 |
| `self.xxx` | `this.xxx` | 实例引用，必须显式声明为第一个参数 |
| `@dataclass` | `@Data` (Lombok) / `record` | 自动生成构造器、toString、equals |
| `@property` | `@Getter` | 把方法变成属性访问 |
| `X \| None` | `Optional<X>` | 可选类型，Python 3.10+ 语法 |
| `x: int = 5` | `int x = 5` | 类型注解（运行时不强制） |
| `def f(x: str) -> bool:` | `boolean f(String x)` | 函数参数/返回值类型注解 |
| `from x import y` | `import x.y` | 导入模块中的特定项 |
| `import x` | `import x.X` | 导入模块 |
| `try: ... except E as e:` | `try { } catch (E e) { }` | 异常处理，没有 checked exception |
| `with open(f) as fh:` | `try (FileReader fr = new FileReader(f))` | 自动资源管理 |
| `if x in list:` | `if (list.contains(x))` | 成员检查 |
| `[x*2 for x in list]` | `list.stream().map(x -> x*2).toList()` | 列表推导式 |
| `{k: v for k, v in d.items()}` | `map.entrySet().stream().collect(...)` | 字典推导式 |
| `lambda x: x*2` | `x -> x * 2` | Lambda 表达式 |
| `@decorator` | 注解（但更强大） | 装饰器，可以包装函数/类，功能比 Java 注解更强 |
| `__init__.py` | `package com.example;` | 标识目录为 Python 包，Java 不需要 |
| `_name` | `private Name` | 下划线开头表示"内部使用"（约定，非强制） |
| `__name` | `private Name` | 双下划线触发名称改写（name mangling） |
| `if __name__ == '__main__':` | `public static void main(String[] args)` | 入口点判断 |
| `yield` | `return` + 状态保留 | 生成器，逐个产生值，可恢复状态 |

### 12.4 关键 Python 概念详解

**1. `async def` / `await`（异步编程）**
```python
# Python 的 async/await 类似于 Java 的 CompletableFuture
async def fetch_data():                # 相当于 CompletableFuture<Data> fetchData()
    result = await db.query(sql)       # 相当于 db.query(sql).get()
    return result                      # 相当于 CompletableFuture.completedFuture(result)
```
- 但 Python 的协程更轻量：不需要线程池，单线程内并发
- 类比：Java 的虚拟线程（Virtual Threads）+ CompletableFuture 的合体

**2. `@dataclass`（数据类）**
```python
@dataclass
class User:                            # 相当于 @Data class User
    id: str                            # private String id;
    name: str                          # private String name;
    age: int = 0                       # private int age = 0;
```
- 自动生成：`__init__`（构造器）、`__repr__`（toString）、`__eq__`（equals/hashCode）
- 可选可变（`field(default_factory=list)` 相当于初始化空列表）

**3. `Protocol`（鸭子类型接口）**
```python
class Embedder(Protocol):              # 相当于 interface Embedder
    async def embed(self, texts): ...  #     CompletableFuture<List<float[]>> embed(List<String> texts);
```
- 不需要显式 `implements`——任何有同名方法的对象自动实现 Protocol
- 类比：Java 的 `interface` + 泛型，但更灵活（结构类型而非名义类型）

**4. `contextvars`（异步上下文）**
```python
_tenant_id = contextvars.ContextVar('tenant_id')  # 相当于 ThreadLocal<String>

def set_current_tenant(tid: str):                 # public static void setCurrentTenant(String tid)
    _tenant_id.set(tid)

def get_current_tenant() -> str | None:           # public static Optional<String> getCurrentTenant()
    return _tenant_id.get(None)
```
- 类比 Java 的 ThreadLocal，但在异步代码中安全（同一协程内保持）
- 类似 Java 的 `TransmittableThreadLocal`（阿里开源，解决线程池传递问题）

**5. `X | None`（可选类型）**
```python
def find_user(id: str) -> User | None:   # 相当于 Optional<User> findUser(String id)
    ...
    return None  if not found            # 相当于 return Optional.empty()
    return user                          # 相当于 return Optional.of(user)
```
- Python 3.10+ 语法，取代旧的 `Optional[X]` 写法
- 比 Java 的 Optional 更简洁：`User | None` vs `Optional<User>`

### 12.5 关键架构概念详解

**1. 什么是 Agent？**
```
AI Agent ≠ Spring Agent ≠ JMX Agent

这里的 Agent 是一个"LLM 驱动的工作流引擎"。可以理解为：
- 一个"智能 Service 层"，接收用户问题，输出 SQL 和图表
- 内部有状态机控制执行流程
- 可以自我修正（SQL 错了就重试）
- 可以问用户（不确定时暂停等待输入）

类比 Java 世界：
- 最接近 Spring State Machine + 策略模式
- 但每个"状态"的执行逻辑是调用 LLM API，而不是 Java 方法
```

**2. 什么是 LLM（大语言模型）？**
```
LLM = Large Language Model，如 GPT-4、Claude、Qwen 等。
可以理解为"一个超级智能的实习生"：
- 你给它一段文字（Prompt），它续写出一段文字（Response）
- 它没有真正的"理解"，但能根据训练数据做出合理的推测
- 它不是 API 接口，不是算法，而是"猜下一个词"的神经网络

系统调用 LLM 的方式：
- 不是 HTTP 请求到某个"AI 服务"
- 是调用 llm_chat() 函数，传入 prompt，返回文本
- 类似于调用一个"超级 String → String 函数"
```

**3. 什么是 Embedding / 向量？**
```
Embedding（嵌入）是把文本变成数字向量的过程。
类似于：
- 不是 MD5（固定哈希，相同输入必然相同输出）
- 不是 Base64（编码，可还原）
- 而是"把意思编码成数字"：语义相似 → 数字距离近

"狗"  → [0.1, 0.3, 0.8, ...]  (1024 个数字)
"猫"  → [0.2, 0.3, 0.7, ...]  (距离近，因为语义相似)
"汽车" → [0.9, 0.1, 0.2, ...]  (距离远，因为语义不相似)

BGE-large-zh-v1.5 是专门做中文嵌入的模型，输出 1024 维向量。
```

**4. 什么是向量数据库？**
```
传统数据库：SELECT * FROM users WHERE name = '张三'
          → 精确匹配，结果 "有" 或 "没有"

向量数据库：SELECT * FROM vec_index ORDER BY distance(query_vec) LIMIT 10
          → 相似度搜索，结果 "最像的 10 个"

类比：
- 向量数据库 ≈ Elasticsearch 的"更像这份"（More Like This）查询
- 但专门为向量相似度搜索优化（HNSW 索引）
- Milvus 是开源的向量数据库，类似 Elasticsearch 但用于向量

为什么需要它？
- 用户的自然语言问题（如"本月销售额"）需要匹配到数据库表名/列名
- 关键词匹配（如"销售"）可能匹配不到（如实际表名是 "orders"）
- 向量搜索能找到语义相似的表/列（"销售额" → "orders.amount"）
```

**5. 什么是 self-healing（自愈）？**
```
SQL 自愈不是"自己修复自己"，而是：
1. 生成 SQL → 执行 → 报错（如"列不存在"）
2. 分析错误信息（"列 'abc' 不存在，可用列: 'name', 'amount'"）
3. 带着错误信息重新调用 LLM 生成修正版 SQL
4. 重新执行 → 最多 2 轮

类比 Java：
- try { execute(sql); } catch (SQLException e) { 
    // 分析错误
    // 调用 LLM 修正 SQL
    // 重试（最多 2 次）
  }
```

### 12.6 快速启动指南（从零开始）

```bash
# 1. 安装 Python 3.12（如已安装可跳过）
#    macOS: brew install python@3.12
#    Windows: 从 python.org 下载
#    建议用 pyenv 管理版本（类似 Java 的 SDKMAN!）

# 2. 安装 uv（Python 包管理器，类比 Maven）
#    macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
#    Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 3. 克隆项目并安装依赖
git clone <repo-url>
cd chat-bi
uv sync                    # 相当于 mvn install（安装所有依赖）

# 4. 配置环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env 填写：LLM_URL、LLM_API_KEY、DATABASE_URL 等

# 5. 启动基础设施（Docker Compose）
docker compose up -d       # 启动 PostgreSQL + Milvus + Redis

# 6. 启动后端
./start-backend.sh         # 相当于 mvn spring-boot:run

# 7. 启动前端（另一个终端）
cd frontend
npm install
npx vite --host 0.0.0.0 --port 5173

# 8. 打开浏览器访问 http://localhost:5173
```

### 12.7 调试技巧

```bash
# 查看日志（相当于 tail -f application.log）
tail -f logs/chatbi.log

# 用 print 调试（相当于 System.out.println）
# 在代码中加入: print(f"xxx = {xxx}")

# 用 pdb 断点调试（相当于手动插入断点）
# 在代码中加入: import pdb; pdb.set_trace()
# 启动后会在该行暂停，进入交互式调试

# 用 IDE 调试：VS Code 或 PyCharm
# 设置断点后，启动后端时加 --reload 参数：
./start-backend.sh --reload

# 跑测试（相当于 mvn test）
uv run pytest backend/tests/ -q

# 跑单个测试文件
uv run pytest backend/tests/test_intent.py -v
```

### 12.8 常见误区

| 误区 | 正确理解 |
|------|---------|
| "Python 没有类型" | Python 有类型注解（`x: int`），但运行时不做强制检查 |
| "`__init__` 是构造器" | 是，但第一个参数是 `self`（实例引用） |
| "`@dataclass` 像 Lombok" | 类似，但 dataclass 是标准库，不需要插件 |
| "Python 的多线程" | 有 GIL 锁，多线程不能并行 CPU 计算，但异步 I/O 高效 |
| "`async def` 像 `@Async`" | 更底层，是协程而非线程池 |
| "Python 没有接口" | 有 `Protocol`（鸭子类型接口）和 `ABC`（抽象基类） |
| "`__init__.py` 像构造器" | 不是，是包标识文件，内容在导入时执行 |
| "`import` 像 Java 的 import" | Java 编译时静态导入，Python 运行时执行导入的代码 |

---

*本文档基于 ChatBI v2 源码分析生成，最后更新于 2026-08-03*