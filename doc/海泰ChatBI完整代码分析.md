# 海泰 ChatBI 完整代码分析

> 基于 24 个 Python 文件 + 5 个 Skills 文件的逐层深度解析
> 分析日期：2026-05-10

---

## 第一部分：项目全景

### 1.1 项目结构树（带说明的目录树）

```
ai/
├── src/
│   ├── main.py                          # [入口] FastAPI 应用 + CopilotKit 集成，管理生命周期和连接池
│   ├── agent/                           # [Agent 层] 多 Agent 协作的核心实现
│   │   ├── leader_agent.py              # Leader Agent：使用 deepagents 创建多 Agent 系统，定义 4 个工具 + 4 个子代理
│   │   ├── context_tools.py             # 状态管理：PostgresStore 读写封装、ContextKey 枚举、序列化工具
│   │   ├── intent_classifier.py         # 意图识别：92 行系统提示词，三分类四动作，输出 Pydantic 结构
│   │   ├── metric_searcher.py           # 指标检索：两阶段向量检索（Milvus + LLM 筛选），复合指标展开
│   │   ├── metric_selection.py          # 指标选择：LLM 从候选中精选，多策略 JSON 提取与修复
│   │   ├── sql_agent.py                 # SQL 生成：强制 5 步流程，Skills 注入，列白名单 + data_type 约束
│   │   ├── visualization_agent.py       # 可视化：LLM 生成 ECharts option，JSON 自愈机制
│   │   └── skills_observer_middleware.py # Skills 可观测中间件：三层拦截（before_agent / after_model / wrap_tool_call）
│   ├── service/                         # [Service 层] Milvus 向量数据库 CRUD
│   │   ├── metric_service.py            # 指标 CRUD：内置 Embedding Function、HNSW 索引、语义检索
│   │   ├── table_service.py             # 表元数据 CRUD：精确过滤查询 + 语义检索
│   │   └── column_service.py            # 列元数据 CRUD：按 table_id 批量查询
│   ├── model/                           # [Model 层] Pydantic 模型 + Milvus Schema + LLM 构造
│   │   └── model.py                     # Model2Schema 基类（ABC），数据模型（MetricInfo/TableInfo/ColumnInfo），get_model 工厂
│   ├── utils/                           # [工具层] 配置、日志、提示词、SQL 工具、ETL
│   │   ├── config.py                    # 环境变量配置 + get_skill_dir 路径解析 + get_embedding_dim 维度映射
│   │   ├── logger.py                    # 日志系统：TimedRotatingFileHandler 按天轮转
│   │   ├── prompt.py                    # Leader 系统提示词：5 个 section（上下文来源/Stage 迁移/固定流程/多轮规则/输出要求）
│   │   ├── sql_utils.py                 # SQL 执行（Oracle + ROWNUM 分页）与只读校验 + 表名提取
│   │   └── etl.py                       # 数据初始化：从业务系统 REST API + MongoDB 同步到 Milvus
│   ├── api/                             # [API 模块] 空，未使用
│   └── tools/                           # [工具模块] 空，未使用
├── skills/                              # [Skills 目录] 业务规则外置
│   ├── sql-business-rules/
│   │   └── SKILL.md                     # SQL 业务规则：statistic_time 格式约束 + IS NOT NULL 非空规则
│   └── chart-describe/
│       ├── SKILL.md                     # 图表生成通用规则：设计原则 + 支持类型 + 下载导出配置
│       └── reference/
│           ├── bar.md                   # 柱状图 ECharts option 模板
│           ├── line.md                  # 折线图 ECharts option 模板
│           └── pie.md                   # 饼图 ECharts option 模板
├── .env                                 # 环境变量配置（LLM / Milvus / Oracle / PostgreSQL / MongoDB / Skills）
├── pyproject.toml                       # 项目依赖（deepagents + LangGraph + Milvus + Oracle + CopilotKit）
├── langgraph.json                       # LangGraph 平台配置
├── Dockerfile                           # Docker 构建（uv + Oracle Instant Client）
└── docker-entrypoint.sh                 # 启动脚本（Skills 目录初始化）
```

### 1.2 完整依赖关系图（ASCII）

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Import 依赖关系图                              │
└──────────────────────────────────────────────────────────────────────────┘

main.py
  ├── agent/leader_agent.py ─────────┬── deepagents (create_deep_agent)
  │   ├── agent/context_tools.py     ├── agent/intent_classifier.py ── model/model.py
  │   │                          ├── agent/metric_searcher.py ──┬── agent/metric_selection.py
  │   │                          │                              ├── service/metric_service.py
  │   │                          │                              ├── service/table_service.py
  │   │                          │                              ├── service/column_service.py
  │   │                          │                              └── utils/sql_utils.py
  │   │                          ├── agent/sql_agent.py ────────┼── agent/context_tools.py
  │   │                          │                              ├── utils/sql_utils.py
  │   │                          │                              └── agent/skills_observer_middleware.py
  │   │                          ├── agent/visualization_agent.py
  │   │                          │    ├── agent/context_tools.py
  │   │                          │    ├── agent/skills_observer_middleware.py
  │   │                          │    └── utils/config.py
  │   │                          ├── utils/prompt.py
  │   │                          └── model/model.py
  │   │
  │   ├── agent/context_tools.py ──── pydantic (BaseModel, Enum)
  │   │
  │   └── utils/etl.py ──────────┬── service/metric_service.py
  │                               ├── service/table_service.py
  │                               ├── service/column_service.py
  │                               ├── model/model.py
  │                               └── utils/sql_utils.py
  │
  ├── utils/config.py ────────────── dotenv, pathlib
  ├── utils/logger.py ────────────── logging, TimedRotatingFileHandler
  └── copilotkit, ag_ui_langgraph, langgraph.checkpoint.postgres, psycopg_pool


service/metric_service.py ──────── pymilvus, openai, model/model.py, utils/config.py
service/table_service.py ───────── pymilvus, model/model.py, utils/config.py
service/column_service.py ──────── pymilvus, model/model.py, utils/config.py

model/model.py ─────────────────── pydantic, abc, langchain_openai, pymilvus, utils/config.py
```

**依赖关系要点**：

1. `main.py` 是所有依赖的根节点，通过 `leader_agent.py` 间接引用了整个 Agent 层
2. `context_tools.py` 是状态管理的核心，被 `leader_agent.py`、`metric_searcher.py`、`sql_agent.py`、`visualization_agent.py` 共同引用
3. `model/model.py` 被几乎所有模块引用（提供 Pydantic 模型和 `get_model()` 工厂）
4. Service 层只被 `metric_searcher.py` 和 `etl.py` 引用，形成了清晰的调用层次
5. `metric_selection.py` 是一个独立模块，仅被 `metric_searcher.py` 引用

### 1.3 技术栈全景

| 层级 | 技术 | 版本 | 选择原因 |
|------|------|------|----------|
| **Web 框架** | FastAPI | >=0.135.1 | 异步支持、自动 OpenAPI 文档、类型安全 |
| **AI 框架** | deepagents | >=0.4.11 | 封装 LangGraph，提供多 Agent 协作、Skills 管理、Middleware 扩展 |
| **图引擎** | LangGraph | (via deepagents) | 状态图管理、检查点持久化、条件路由 |
| **LLM** | ChatOpenAI (Qwen3-30B) | langchain-openai >=1.1.12 | 兼容 OpenAI API 协议，支持结构化输出 |
| **向量数据库** | Milvus | pymilvus >=2.6.10 | 专业向量检索，内置 Embedding Function，HNSW 索引 |
| **Embedding** | BGE-large-zh-v1.5 | (via Milvus Function) | 中文语义向量模型，1024 维，开源免费 |
| **业务数据库** | Oracle | oracledb >=3.4.2 | 目标业务系统的数据库类型（医院 HIS 系统） |
| **元数据存储** | MongoDB | pymongo >=4.16.0 | 存储表结构和列信息的原始数据源 |
| **状态持久化** | PostgreSQL | langgraph-checkpoint-postgres >=3.0.5 | LangGraph Checkpointer + PostgresStore 双重持久化 |
| **前端集成** | CopilotKit | >=0.1.85 | 开箱即用的 AI 对话前端组件库 |
| **通信协议** | ag-ui-langgraph | (via copilotkit) | Agent UI 标准协议，流式响应 + 工具调用展示 |
| **包管理** | uv | (Docker 中使用) | Rust 实现的高速 Python 包管理器 |
| **日志** | Python logging + TimedRotatingFileHandler | 标准库 | 按天轮转，保留 14 天，零依赖 |

**技术栈选择逻辑**：

- **deepagents 而非原生 LangGraph**：deepagents 在 LangGraph 之上封装了子代理委派（task 工具自动注册）、Skills 自动注入、Middleware 钩子等复杂度，让开发者只需定义子代理字典和工具函数，无需手动构建 StateGraph
- **Milvus 而非 pgvector**：Milvus 专为向量检索设计，内置 Embedding Function（插入数据时自动调 Embedding API 生成向量），HNSW 索引在大规模数据上性能更优
- **Oracle 而非 MySQL**：目标业务系统是医院 HIS 系统，底层数据库固定为 Oracle，必须兼容 Oracle 方言
- **PostgreSQL 双角色**：既作为 LangGraph 的 Checkpointer（保存会话状态），又作为 PostgresStore（保存 Agent 中间结果），一个数据库承担两种职责

### 1.4 环境配置解析

`.env` 文件包含 30 行配置，按功能分组如下：

#### LLM 配置

| 变量 | 默认值 | 作用 | 设计考量 |
|------|--------|------|----------|
| `LLM_URL` | 无 | vLLM 服务地址 | 自部署 vLLM，兼容 OpenAI API 协议 |
| `LLM_MODEL` | 无 | 模型名称（如 Qwen3-30B） | 通过 ChatOpenAI 统一接口调用 |
| `API_KEY` | 无 | API 密钥 | 本地 vLLM 通常设为 `EMPTY` |
| `LLM_MAX_TOKENS` | 65536 | 最大输出 token 数 | 设为 65536（远大于我方的 2000），确保复杂 SQL 和 ECharts option 不被截断 |

#### Embedding 配置

| 变量 | 默认值 | 作用 | 设计考量 |
|------|--------|------|----------|
| `EMBEDDING_URL` | 无 | Embedding API 地址 | 与 LLM 服务分开部署，可用 GPU 优化 |
| `EMBEDDING_MODEL` | 无 | Embedding 模型名称 | 支持 BGE-large-zh（1024维）和 Qwen3-Embedding（4096维） |
| `EMBEDDING_API_KEY` | 无 | Embedding API 密钥 | 独立密钥管理 |

#### 存储配置

| 变量 | 默认值 | 作用 | 设计考量 |
|------|--------|------|----------|
| `MILVUS_URL` | 无 | Milvus 连接地址 | 支持分布式部署 |
| `ORACLE_URI` | 无 | Oracle 连接串 | 格式：`user/password@host:port/service` |
| `ORACLE_CLIENT_LIB_DIR` | 无 | Oracle Instant Client 路径 | Docker 中固定为 `/app/instantclient_19_30` |
| `POSTGRES_URI` | 无 | PostgreSQL 连接串 | 同时用于 Checkpointer 和 Store |
| `MONGODB_URI` | 无 | MongoDB 连接串 | 读取表结构元数据 |
| `METRIC_URI` | 无 | 业务系统 REST API 地址 | 指标定义的来源系统 |

#### Skills 配置

| 变量 | 默认值 | 作用 | 设计考量 |
|------|--------|------|----------|
| `SKILL_DIR` | 无 | Skills 根目录 | Docker 中为 `/home/skills`，支持挂载卷热更新 |

#### 日志配置

| 变量 | 默认值 | 作用 | 设计考量 |
|------|--------|------|----------|
| `LOG_LEVEL` | INFO | 日志级别 | 生产环境可设为 WARNING |
| `LOG_DIR` | /deps/ai/logs | 日志目录 | Docker 中挂载卷持久化 |
| `LOG_RETENTION_DAYS` | 14 | 日志保留天数 | 按天轮转，14 天后自动清理 |

**设计考量总结**：

1. **LLM_MAX_TOKENS = 65536**：这是因为 SQL 生成和 ECharts option 生成都需要大量输出空间，特别是复合指标涉及多表 JOIN 时 SQL 可能很长。我方设为 2000 在复杂场景下容易截断。
2. **独立 Embedding 配置**：Embedding 服务与 LLM 服务分开部署，可独立扩展，避免 Embedding 调用影响 LLM 推理性能。
3. **SKILL_DIR 独立于代码目录**：Docker 中 Skills 放在 `/home/skills`（通过卷挂载），代码在 `/app`，修改 Skills 无需重新构建镜像。

---

## 第二部分：架构设计深度解析

### 2.1 整体架构图（ASCII）

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               整体数据流架构                                     │
└─────────────────────────────────────────────────────────────────────────────────┘

   用户（浏览器）
       │
       │  ag-ui-langgraph 协议（流式）
       ▼
┌──────────────┐     ┌──────────────────────────────────────────────────────────┐
│   FastAPI     │     │                   Leader Agent                           │
│   (main.py)   │     │                                                          │
│               │     │  ┌────────────────────────────────────────────────────┐ │
│  lifespan()   │────▶│  │ tools: [get_context_by_key, get_intent_history,   │ │
│  ├─ 连接池    │     │  │          append_intent_history, now_time]          │ │
│  ├─ Checkpt   │     │  └────────────────────────────────────────────────────┘ │
│  ├─ Store     │     │                                                          │
│  └─ Agent     │     │  ┌──── 子代理 1 ────┐  ┌──── 子代理 2 ────┐           │
│               │     │  │ intent_classifier │  │ metric_searcher  │           │
│  /api/init-*  │     │  │ (意图识别)        │  │ (指标检索)       │           │
│               │     │  └──────────────────┘  └──────┬───────────┘           │
│               │     │                                │                      │
│               │     │  ┌──── 子代理 3 ────┐  ┌──────▼───────────┐           │
│               │     │  │   sql_agent       │  │ visualization_   │           │
│               │     │  │ (SQL 生成+执行)   │  │ agent (可视化)   │           │
│               │     │  └──────────────────┘  └──────────────────┘           │
│               │     └──────────────┬───────────────────────────────────────┘
│               │                    │
└──────────────┘                    │
                                    ▼
              ┌─────────────────────┼──────────────────────┐
              │                     │                      │
    ┌─────────▼────────┐  ┌────────▼────────┐  ┌─────────▼────────┐
    │   PostgreSQL      │  │    Milvus       │  │    Oracle        │
    │   (Checkpointer   │  │  (指标向量库     │  │  (业务数据)      │
    │    + Store)       │  │   + 表结构)     │  │                  │
    └──────────────────┘  └────────────────┘  └──────────────────┘
              ▲                     ▲
              │                     │
    ┌─────────┴────────┐  ┌────────┴────────┐
    │   MongoDB        │  │  业务系统 REST   │
    │  (表/列元数据)    │  │  API (指标定义)  │
    └──────────────────┘  └─────────────────┘
```

### 2.2 Leader-Worker 多 Agent 架构

#### 设计思路：为什么用多 Agent 而不是单管道？

海泰选择 Leader-Worker 多 Agent 架构而非单层线性管道，基于以下考量：

1. **职责隔离**：每个子代理（intent_classifier、metric_searcher、sql_agent、visualization_agent）只负责一个明确任务，system_prompt 更短更聚焦，LLM 理解更准确
2. **独立调试**：每个子代理可以独立测试（通过 `/api/test` 端点传入特定 thread_id），问题定位更快
3. **Skills 按需挂载**：不同子代理挂载不同的 Skills（sql_agent 挂载 sql-business-rules，visualization_agent 挂载 chart-describe），避免无关规则干扰
4. **模型可差异化**：不同子代理可以使用不同模型（虽然当前都使用同一个 Qwen3-30B），未来可以给意图识别用小模型、SQL 生成用大模型

**对比我方**：我方使用单层 `StateGraph(QueryState)` + 6 个节点函数，所有节点共享同一个 LLM 实例和同一套提示词上下文。优点是简单直观，缺点是节点间的耦合度高，修改一个节点可能影响其他节点。

#### deepagents 框架做了什么？

`create_deep_agent` 是 deepagents 库的核心函数，它封装了以下复杂度：

1. **StateGraph 自动构建**：开发者只需传入 `subagents` 列表，框架自动创建 Leader 的 StateGraph，注册所有子代理为 `task` 工具
2. **task 工具自动注册**：每个子代理自动变成 Leader 可以调用的 `task(subagent_type="xxx", description="...")` 工具
3. **上下文自动传递**：Leader 的 `context_schema`（即 `Context(user_id, user_name)`）自动传递给所有子代理
4. **Store 自动注入**：`store` 参数自动注入到所有子代理的 `ToolRuntime` 中
5. **Checkpointer 自动绑定**：`checkpointer` 参数自动绑定到 StateGraph，实现会话状态持久化
6. **Skills 自动加载**：子代理字典中的 `skills` 字段指定路径，框架在 Agent 执行前自动读取 SKILL.md 并注入系统提示词
7. **Middleware 自动拦截**：`middleware` 列表中的中间件会在 Agent 生命周期各阶段自动调用

**核心代码**（leader_agent.py 行 87-105）：

```python
def build_leader_agent(*, checkpointer, store, debug=True):
    return create_deep_agent(
        name="leader_agent",
        context_schema=Context,           # 请求级上下文
        backend=BACKEND,                  # FilesystemBackend（读取 Skills 文件）
        model=get_model(),                # LLM 实例
        system_prompt=leader_system_prompt,
        tools=[get_context_by_key, get_intent_history, append_intent_history, now_time],
        subagents=[intent_classifier, metric_searcher, sql_agent, visualization_agent],
        middleware=[],
        debug=debug,
        checkpointer=checkpointer,
        store=store,
    )
```

#### 子代理的 task 工具是如何自动注册的？

deepagents 框架在 `create_deep_agent` 内部，遍历 `subagents` 列表，为每个子代理字典自动生成一个 `task` 工具。生成的工具名称格式为 `task_<subagent_name>`，参数为 `{description: str}`。

例如，`intent_classifier` 子代理会生成：

```python
# 框架内部自动生成（伪代码）
@tool("task_intent_classifier", description="轻量路由：bi_query / chart_only / life 三分类")
def task_intent_classifier(description: str, runtime: ToolRuntime[Context]):
    # 创建子代理的 StateGraph
    # 传入 description 作为任务描述
    # 子代理执行完成后返回 {ok: bool, message: str}
```

Leader 在系统提示词的指导下，通过调用这些 `task` 工具来委派子代理。

#### Leader 如何通过 task 工具委派子代理？

Leader 的系统提示词（prompt.py）定义了严格的 stage 迁移规则：

```
intent → metric_search → metric_explain → generate_sql → execute_sql → visual → final
```

Leader 在每个 stage 调用对应的 task 工具，并通过 `description` 参数传递任务上下文。关键设计是：

1. **子代理只返回简短结果**：`{ok: bool, message: str}`，不返回大量数据
2. **详细数据通过 Store 读写**：子代理通过 `put_context()` 写入，Leader 通过 `get_context_by_key()` 读取
3. **description 模板化**：Leader 必须使用固定的 description 格式，例如 `task(subagent_type="intent_classifier", description="请对以下用户问题进行意图识别：{question}")`

#### 子代理之间如何通过 Store 通信（不直接通信）？

子代理之间不直接通信，而是通过 PostgresStore 作为中间媒介：

```
intent_classifier ──写入──▶ Store(intent_history)
                              │
metric_searcher  ──写入──▶ Store(metric_context, tables_context)
                              │
sql_agent        ──读取──▶ Store(metric_context, tables_context)
                 ──写入──▶ Store(zip_table_context, sql_context, sql_execute)
                              │
visualization   ──读取──▶ Store(sql_execute)
_agent          ──写入──▶ Store(visualization_context)
```

**为什么不让子代理直接通信？**

1. **解耦**：每个子代理只关心自己的输入和输出，不依赖其他子代理的实现
2. **可恢复**：如果 SQL 执行失败，可以从 Store 读取 metric_context 重新调用 sql_agent，无需重新检索指标
3. **可调试**：通过查看 Store 中的数据，可以精确定位哪个阶段出了问题
4. **可扩展**：新增子代理只需读取 Store 中已有的数据，无需修改其他子代理

### 2.3 状态管理架构

#### PostgresStore 的 Namespace 设计

**Namespace = (user_id, thread_id)**

```python
# context_tools.py 行 63-70
def _build_namespace(runtime: ToolRuntime[Context]) -> Tuple:
    agent_context = _get_context(runtime)
    thread_id = _get_thread_id(runtime)
    return (
        "test_user_id" if not agent_context or not agent_context.user_id else agent_context.user_id,
        thread_id
    )
```

**为什么用双层 Namespace？**

1. **第一层 user_id**：用户隔离。不同用户的数据完全隔离，用户 A 无法读取用户 B 的 metric_context
2. **第二层 thread_id**：会话隔离。同一用户的不同对话（不同 thread_id）互不干扰
3. **PostgresStore 内部实现**：Namespace 映射到 PostgreSQL 表的联合主键，查询效率高

**示例**：

```
Namespace: ("user_123", "thread_abc")
  ├── metric_context    → [MetricContext(门诊处方合格率, ...)]
  ├── tables_context    → [TableContext(PRESCRIPTION, ...)]
  ├── zip_table_context → [TableContext(PRESCRIPTION, column=[精简列...])]
  ├── sql_context       → "SELECT ..."
  ├── sql_execute       → {columns: [...], rows: [...]}
  ├── visualization_context → {ECHARTS_OPTION: {...}}
  └── intent_history    → [{normalized_question: "...", user_question: "..."}]

Namespace: ("user_123", "thread_xyz")
  └── （另一个会话的独立数据）
```

#### ContextKey 枚举：7 个状态键各自的作用和生命周期

```python
# context_tools.py 行 24-31
class ContextKey(Enum):
    metric_context = "metric_context"           # 指标定义（含复合指标展开后的子指标）
    tables_context = "tables_context"           # 完整表结构（所有列）
    zip_table_context = "zip_table_context"     # 精简表结构（LLM 筛选后的列）
    sql_context = "sql_context"                 # SQL 文本
    sql_execute = "sql_execute"                 # 执行结果（columns + rows）
    visualization_context = "visualization_context"  # 图表配置（ECharts option）
    intent_history = "intent_history"           # 历史意图（追问用，最多 10 条）
```

| ContextKey | 写入者 | 读取者 | 生命周期 | 设计考量 |
|------------|--------|--------|----------|----------|
| `metric_context` | metric_searcher | sql_agent | 单次查询有效 | 包含指标的 SQL 模板、公式、表名 |
| `tables_context` | metric_searcher | sql_agent | 单次查询有效 | 完整列信息，用于 LLM 精简 |
| `zip_table_context` | sql_agent | sql_agent（自身） | 单次查询有效 | 精简后只保留相关列，减少 token |
| `sql_context` | sql_agent | Leader, visualization_agent | 单次查询有效 | 存储最终 SQL |
| `sql_execute` | sql_agent | visualization_agent | 单次查询有效 | 包含 columns + rows |
| `visualization_context` | visualization_agent | Leader → 前端 | 单次查询有效 | ECharts option JSON |
| `intent_history` | Leader（append_intent_history） | Leader（get_intent_history） | **跨查询持久** | 最多 10 条，追问补全的数据源 |

**关键设计**：`intent_history` 是唯一跨查询持久的状态键，它支持追问场景中继承历史维度。

#### Pydantic 模型约束：MetricContext 递归结构的设计思路

```python
# context_tools.py 行 37-46
class MetricContext(BaseModel):
    metric_name: str
    metric_unit: str
    metric_type: Literal["single", "composite"]
    metric_sql: str
    calc_expression: str
    sub_metrics: List["MetricContext"]       # 递归引用自身
    table_full_names: List[str]
```

**为什么用递归结构？**

复合指标可以由子指标组成，而子指标本身也可以是复合指标（虽然当前代码不支持嵌套复合指标，见 metric_searcher.py 行 186-187 的注释）。递归结构预留了扩展空间。

**实际使用场景**：

```
指标：门诊处方合格率
  ├── metric_type: "composite"
  ├── calc_expression: "门诊合格处方人次数 / 门诊处方人次数 * 100"
  ├── sub_metrics:
  │   ├── MetricContext(门诊合格处方人次数, type=single, sql=SELECT ...)
  │   └── MetricContext(门诊处方人次数, type=single, sql=SELECT ...)
  └── table_full_names: ["SCHEMA.T1", "SCHEMA.T2"]
```

**限制**：当前代码中复合指标的子指标只能是单一指标，不支持嵌套复合指标（即子指标不能再有自己的 sub_metrics）。这是因为 `build_metric_context` 函数中递归调用时，子指标的 `sub_metrics` 始终设为空列表。

#### _to_jsonable 序列化：为什么需要处理 Decimal/datetime/memoryview？

```python
# context_tools.py 行 77-92
def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)              # Oracle 返回 Decimal 类型
    if isinstance(value, (datetime, date)):
        return value.isoformat()         # 日期转 ISO 字符串
    if isinstance(value, BaseModel):
        return _to_jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    return str(value)
```

**为什么需要这个函数？**

PostgresStore 的 `put()` 方法要求 value 必须是 JSON 可序列化的。但实际运行中会遇到以下非 JSON 类型：

1. **`Decimal`**：Oracle 数据库查询结果中的数值字段返回 `Decimal` 而非 `float`
2. **`datetime`/`date`**：Oracle 日期字段返回 Python datetime 对象
3. **`memoryview`**：PostgreSQL JSONB 字段读取时可能返回 `memoryview` 类型
4. **`BaseModel`**：Pydantic 模型需要先转为 dict

#### Store 的读写流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                     Store 读写流程                               │
└─────────────────────────────────────────────────────────────────┘

  ┌──────────┐                                                    │
  │ 子代理调用 │                                                    │
  │ put_context│                                                   │
  └─────┬────┘                                                    │
        │                                                         │
        ▼                                                         │
  ┌──────────────────┐     ┌───────────────────┐                 │
  │ _build_namespace  │     │ _to_jsonable(value)│                 │
  │ (user_id, thread  │     │ 递归序列化         │                 │
  │  _id)             │     │ Decimal→float     │                 │
  └────────┬─────────┘     │ datetime→ISO      │                 │
           │               └─────────┬─────────┘                 │
           │                         │                           │
           ▼                         ▼                           │
  ┌──────────────────────────────────────────────────────┐       │
  │              PostgresStore.put()                      │       │
  │  namespace = (user_id, thread_id)                    │       │
  │  key = "metric_context"                              │       │
  │  value = {序列化后的 JSON}                             │       │
  │                                                       │       │
  │  SQL: INSERT INTO store (namespace, key, value)      │       │
  │       VALUES (...) ON CONFLICT UPDATE                 │       │
  └──────────────────────────────────────────────────────┘       │
                                                                  │
  ┌──────────┐                                                    │
  │ 子代理调用 │                                                    │
  │ get_context│                                                   │
  └─────┬────┘                                                    │
        │                                                         │
        ▼                                                         │
  ┌──────────────────┐     ┌───────────────────┐                 │
  │ _build_namespace  │     │ _store_deserializer│                 │
  │ (user_id, thread  │     │ memoryview→bytes  │                 │
  │  _id)             │     │ bytearray→bytes   │                 │
  └────────┬─────────┘     └─────────┬─────────┘                 │
           │                         │                           │
           ▼                         ▼                           │
  ┌──────────────────────────────────────────────────────┐       │
  │              PostgresStore.get()                      │       │
  │  SQL: SELECT value FROM store                        │       │
  │       WHERE namespace = ? AND key = ?                 │       │
  │                                                       │       │
  │  返回 Item(value={JSON 数据})                         │       │
  └──────────────────────────────────────────────────────┘       │
```

### 2.4 会话持久化架构

#### PostgreSQL Checkpointer 的工作原理

LangGraph 的 Checkpointer 机制在每个节点执行后自动保存完整的图状态到 PostgreSQL。这意味着：

1. **每一步都有快照**：intent_classifier 执行后保存一次，metric_searcher 执行后保存一次，以此类推
2. **可恢复**：如果服务在 SQL 执行过程中崩溃，重启后可以从上一个成功的 checkpoint 恢复
3. **时间旅行**：可以回到任意历史 checkpoint 查看当时的完整状态

**核心代码**（main.py 行 37-53）：

```python
# 异步连接池用于 Checkpointer
async_pool = AsyncConnectionPool(
    POSTGRES_URI,
    min_size=1, max_size=10,
    kwargs={
        "autocommit": True,
        "prepare_threshold": 0,   # 禁用预处理语句缓存
        "row_factory": dict_row,  # 返回字典格式
    },
)
checkpointer = AsyncPostgresSaver(async_pool)
await checkpointer.setup()  # 自动创建 checkpoint 表
```

#### thread_id 如何实现会话隔离

每个对话有一个唯一的 `thread_id`，它通过 LangGraph 的 `configurable` 机制传递：

```python
# main.py 行 82-85（测试端点）
result = agent.stream(
    input={"messages": [{"role": "user", "content": message}]},
    config={"configurable": {"thread_id": thread_id}},
    context=Context(user_id="123", user_name="张三")
)
```

**thread_id 的作用**：

1. **Checkpointer 层面**：不同 thread_id 的 checkpoint 存储在不同的行中，互不干扰
2. **Store 层面**：thread_id 是 Namespace 的第二层，确保不同对话的 Store 数据隔离
3. **多轮对话**：同一 thread_id 的多轮对话共享 checkpoint 和 Store，实现追问

#### InMemorySaver vs AsyncPostgresSaver 的对比

| 维度 | InMemorySaver | AsyncPostgresSaver |
|------|---------------|---------------------|
| **持久化** | 内存，进程退出即丢失 | PostgreSQL，永久保存 |
| **并发安全** | 单进程安全 | 数据库级别锁，多进程安全 |
| **适用场景** | 本地测试、单次调试 | 生产环境、多实例部署 |
| **配置** | `InMemorySaver()` | `AsyncPostgresSaver(async_pool)` |
| **使用位置** | `leader_agent.py` 行 109 的内存模式实例 | `main.py` 行 37 的 lifespan 函数 |

**海泰的设计**：内存模式实例用于 `/api/test` 端点（开发调试），PostgreSQL 模式用于 CopilotKit 集成的生产端点。

#### 会话恢复场景的流程图

```
┌─────────────────────────────────────────────────────────────┐
│                   会话恢复流程                                │
└─────────────────────────────────────────────────────────────┘

  场景：用户第一轮查询了"产科门诊处方合格率"，第二轮追问"上个月呢"

  第一轮：
  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ 用户提问  │───▶│ intent   │───▶│ metric   │───▶│ sql      │
  │          │    │ classifier│   │ searcher │    │ agent    │
  └──────────┘    └──────────┘    └──────────┘    └──────────┘
       │                                               │
       │         Checkpointer 自动保存每步状态           │
       │         Store 写入: metric_context, sql_execute │
       │         intent_history ← [{normalized_question: │
       │           "产科在2024年11月门诊处方合格率"}]      │
       ▼                                               ▼
  ┌──────────────────────────────────────────────────┐
  │              PostgreSQL Checkpoint                │
  │  thread_abc, step=0: {messages: [...]}           │
  │  thread_abc, step=1: {messages: [...], state:..}│
  │  thread_abc, step=2: {messages: [...], state:..}│
  │  thread_abc, step=3: {messages: [...], state:..}│
  └──────────────────────────────────────────────────┘

  第二轮（追问）：
  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ 用户追问  │───▶│ Leader   │───▶│ intent   │───▶│ metric   │
  │"上个月呢" │    │ 读取     │    │ classifier│   │ searcher │
  └──────────┘    │ intent_  │    │ 使用     │    │          │
                  │ history  │    │ intent_  │    │          │
                  │ 补全为:   │    │ history  │    │          │
                  │ "产科在   │    │ 展开     │    │          │
                  │ 2024年10月│    │ 相对时间  │    │          │
                  │ 门诊处方  │    │          │    │          │
                  │ 合格率"   │    │          │    │          │
                  └──────────┘    └──────────┘    └──────────┘
```

### 2.5 Skills 机制架构

#### SKILL.md 的格式和加载流程

SKILL.md 文件由三部分组成：

1. **Front Matter（YAML 元数据）**：

```markdown
---
name: sql-business-rules
description: 生成 SQL 的业务场景规则集合
version: 1.5.1
---
```

2. **正文（Markdown 格式的规则）**：

```markdown
## 时间与 `statistic_time`

1. 只要 SQL 使用了 `statistic_time` 过滤，就不要再使用 `time_dime` 条件
2. `statistic_time` 必须使用 8 位 `YYYYMMDD` 格式
```

3. **引用文件（reference/）**：

```
skills/chart-describe/
├── SKILL.md          # 通用规则
└── reference/
    ├── bar.md        # 柱状图模板
    ├── line.md       # 折线图模板
    └── pie.md        # 饼图模板
```

#### deepagents 如何自动注入 Skills

当子代理字典中包含 `skills` 字段时：

```python
# sql_agent.py
sql_agent = {
    "name": "sql_agent",
    "skills": [get_skill_dir("sql-business-rules")],  # Skills 路径
    "middleware": [SkillsObserverMiddleware("sql_agent")],
    ...
}
```

deepagents 框架在 Agent 执行前会：

1. 解析 `skills` 路径，读取 `SKILL.md` 文件
2. 如果存在 `reference/` 目录，一并读取所有 `.md` 文件
3. 将 Skills 内容注入到 LLM 的系统提示词中（作为额外上下文）
4. LLM 通过 `read_file` 工具读取 Skills 内容

#### SkillsObserverMiddleware 的三层观测设计

```python
# skills_observer_middleware.py
class SkillsObserverMiddleware(AgentMiddleware):

    def before_agent(self, state, runtime):
        """第一层：Agent 执行前，检查 Skills 是否加载到 state"""
        skills_metadata = state.get("skills_metadata", [])
        # 记录加载了哪些 Skills

    def after_model(self, state, runtime):
        """第二层：LLM 响应后，检查 LLM 是否调用了 read_file 读取 Skills"""
        last_ai = next(msg for msg in reversed(messages) if isinstance(msg, AIMessage))
        tool_calls = last_ai.tool_calls
        read_skill_paths = [call for call in tool_calls
                           if call.name == "read_file" and "/skills/" in call.path]
        # 记录是否使用了 Skills

    def wrap_tool_call(self, request, handler):
        """第三层：工具调用时，拦截 read_file 调用并记录"""
        if request.tool_call.name == "read_file" and "/skills/" in request.path:
            logger.info("正在读取 Skills: %s", request.path)
        return handler(request)
```

**三层观测的设计意义**：

| 层级 | 时机 | 检查内容 | 发现什么问题 |
|------|------|----------|------------|
| before_agent | Agent 启动前 | Skills 是否加载到 state | 配置错误导致 Skills 未挂载 |
| after_model | LLM 响应后 | LLM 是否主动读取了 Skills | LLM 忽略了 Skills 规则 |
| wrap_tool_call | read_file 执行时 | 具体读取了哪个 Skills 文件 | 读取了错误的 Skills |

#### 为什么 Skills 比硬编码提示词更好？

| 维度 | 硬编码提示词 | Skills 机制 |
|------|------------|------------|
| **修改方式** | 修改 Python 代码 | 修改 Markdown 文件 |
| **是否重启** | 需要重启服务 | 无需重启（热更新） |
| **环境适配** | 所有环境同一规则 | 不同环境不同 SKILL.md |
| **版本管理** | 在 Git 中跟踪 | 在 Git 中跟踪（但部署时可覆盖） |
| **可观测性** | 无法知道 LLM 是否遵循 | Middleware 记录使用情况 |
| **非开发者友好** | 需要懂 Python | 只需懂 Markdown |

#### 热更新流程图

```
┌──────────────────────────────────────────────────────────────────┐
│                     Skills 热更新流程                             │
└──────────────────────────────────────────────────────────────────┘

  开发者修改 SKILL.md
       │
       ▼
  ┌────────────────────┐
  │ Docker Volume 挂载  │  SKILL_DIR=/home/skills (Volume Mount)
  │ /home/skills/       │  ↓ 文件变更立即反映
  │ └── sql-business-  │
  │     rules/          │
  │     └── SKILL.md   │  ← 修改此文件
  └────────────────────┘
       │
       │ 下一次请求到达
       ▼
  ┌────────────────────┐     ┌────────────────────┐
  │ deepagents 框架    │────▶│ 读取 SKILL.md      │
  │ 自动加载 Skills    │     │ 获取最新内容        │
  └────────────────────┘     └────────────────────┘
       │
       │ 注入到 LLM 上下文
       ▼
  ┌────────────────────┐
  │ LLM 使用最新规则    │  ← 无需重启服务
  │ 生成 SQL           │
  └────────────────────┘
```

### 2.6 CopilotKit 前端集成架构

#### ag-ui-langgraph 协议的设计思路

ag-ui-langgraph 定义了一套标准化的 Agent UI 通信协议，解决以下问题：

1. **流式传输**：Agent 的每一步思考、工具调用、最终结果都以流式方式推送给前端
2. **状态同步**：前端可以实时看到 Agent 正在执行哪个子代理
3. **工具调用展示**：前端可以展示 Agent 调用了哪些工具、参数是什么、返回了什么

#### LangGraphAGUIAgent 如何包装 LangGraph Agent

```python
# main.py 行 67-76
add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        config={"recursion_limit": 50},  # 最大递归深度
        name="deep-agent",
        description="根据用户自然语言，基于指标生成图表的智能体",
        graph=graph,                      # LangGraph CompiledGraph
    ),
    path="/",
)
```

`LangGraphAGUIAgent` 是一个适配器，它：

1. 接收 CopilotKit 前端发送的 HTTP 请求
2. 转换为 LangGraph 的 `graph.stream()` 调用
3. 将 LangGraph 的事件流转换为 ag-ui 协议的事件流
4. 推送给前端

#### 前后端通信时序图

```
┌──────────┐         ┌──────────┐         ┌──────────┐         ┌──────────┐
│  前端     │         │ FastAPI  │         │ Leader   │         │ 子代理   │
│ CopilotKit│         │ main.py │         │ Agent    │         │          │
└────┬─────┘         └────┬─────┘         └────┬─────┘         └────┬─────┘
     │                    │                    │                    │
     │ POST /             │                    │                    │
     │ {messages, thread_ │                    │                    │
     │  id, context}      │                    │                    │
     │───────────────────▶│                    │                    │
     │                    │                    │                    │
     │                    │ graph.stream()     │                    │
     │                    │───────────────────▶│                    │
     │                    │                    │                    │
     │ SSE: agent_start   │                    │                    │
     │◀───────────────────│                    │                    │
     │                    │                    │ task(intent_       │
     │                    │                    │ classifier)        │
     │                    │                    │───────────────────▶│
     │                    │                    │                    │
     │ SSE: tool_call     │                    │◀───────────────────│
     │ {name: "task_..."} │◀───────────────────│                    │
     │◀───────────────────│                    │                    │
     │                    │                    │                    │
     │ SSE: tool_result   │                    │ intent_classifier  │
     │ {ok: true}         │                    │ 完成               │
     │◀───────────────────│                    │                    │
     │                    │                    │                    │
     │                    │                    │ task(metric_       │
     │                    │                    │ searcher)          │
     │                    │                    │───────────────────▶│
     │ ...                │                    │                    │
     │                    │                    │                    │
     │ SSE: final_result  │                    │                    │
     │ {text, ECHARTS_    │                    │                    │
     │  OPTION}           │                    │                    │
     │◀───────────────────│◀───────────────────│                    │
     │                    │                    │                    │
```

---

## 第三部分：核心流程逐行解析

### 3.1 应用启动流程

**main.py 的 lifespan 函数**（行 35-65）：

```
应用启动
  │
  ├── 1. 创建 AsyncConnectionPool（异步，用于 Checkpointer）
  │      min_size=1, max_size=10
  │      autocommit=True            ← 避免事务管理复杂度
  │      prepare_threshold=0        ← 禁用预处理语句缓存（连接池模式下避免状态不一致）
  │      row_factory=dict_row       ← 返回字典格式
  │
  ├── 2. 创建 ConnectionPool（同步，用于 PostgresStore）
  │      参数同上，但为同步版本
  │      为什么需要两个池？
  │      - AsyncPostgresSaver 只接受异步连接池
  │      - PostgresStore 只接受同步连接池
  │
  ├── 3. 创建 AsyncPostgresSaver(async_pool)
  │      自动创建 checkpoints 表
  │      await checkpointer.setup()
  │
  ├── 4. 创建 PostgresStore(sync_pool, deserializer=_store_deserializer)
  │      自动创建 store 表
  │      store.setup()
  │      _store_deserializer 处理 memoryview/bytearray 类型
  │
  ├── 5. build_leader_agent(checkpointer=..., store=..., debug=True)
  │      调用 create_deep_agent 构建完整的多 Agent 系统
  │
  ├── 6. add_langgraph_fastapi_endpoint()
  │      注册 "/" 路由
  │      LangGraphAGUIAgent 包装 graph
  │      recursion_limit=50（防止无限循环）
  │
  └── 7. yield（应用运行中）
         │
         finally:
         ├── async_pool.close()
         └── sync_pool.close()
```

**关键设计点**：

1. **双连接池**：异步池给 Checkpointer（LangGraph 内部用 async），同步池给 Store（PostgresStore 的 API 是同步的）。这是 LangGraph 生态的现状，Checkpointer 提供了异步版本，Store 只有同步版本。

2. **prepare_threshold=0**：PostgreSQL 默认会缓存预处理语句（prepared statements），在连接池模式下可能导致"prepared statement does not exist"错误（因为连接可能被复用到不同的后端进程）。设为 0 禁用这个缓存。

3. **recursion_limit=50**：Leader Agent 调用子代理是通过工具调用实现的，每次调用消耗一层递归。一个完整的查询流程（intent → metric → sql → visualization）至少需要 4 层，加上 LLM 可能的工具调用重试，50 层是安全上限。

### 3.2 意图识别流程

#### 92 行系统提示词逐条解读

`intent_classifier.py` 的 `system_prompt` 共 92 行，是整个系统中最精细的提示词。核心结构如下：

**第一部分：角色定义与任务说明**

```
你是 ChatBI 的"轻量路由"意图识别代理。你只做三分类，不做指标命中判断。
```

设计思路：明确告知 LLM 不要越权做指标匹配，只做分类路由。这是职责隔离原则的体现。

**第二部分：三分类定义**

| intent_type | 含义 | 示例 |
|-------------|------|------|
| `bi_query` | 业务数据查询 | "产科门诊处方合格率" |
| `chart_only` | 纯图表请求 | "用饼图展示"（无数据查询意图） |
| `life` | 闲聊、非 BI | "今天天气怎么样" |

**第三部分：四种 action 定义**

| action | 含义 | 对应下游处理 |
|--------|------|-------------|
| `query` | 数值查询 | metric_searcher → sql_agent → visualization_agent |
| `explain` | 指标解释 | metric_searcher → 直接返回指标定义 |
| `chart` | 作图 | metric_searcher → sql_agent → visualization_agent |
| `other` | 其他 | 直接结束 |

**第四部分：intent_type × action 组合规则**

```
若用户明确说了"解释/定义/含义"→ intent_type=bi_query, action=explain
若用户明确要求"画图/折线图/柱状图"→ intent_type=bi_query 或 chart_only, action=chart
若用户只是问数值 → intent_type=bi_query, action=query
```

#### IntentClassifierOutput 的 8 个字段各自的设计考量

```python
class IntentClassifierOutput(BaseModel):
    intent_type: Literal["bi_query", "chart_only", "life"]  # 三分类，用 Literal 强制枚举
    action: Literal["query", "explain", "chart", "other"]   # 四动作，决定下游路由
    is_bi: bool                          # 快速判断：是否是 BI 查询
    chart_requested: bool = False        # 是否需要图表（前端渲染依据）
    confidence: float = Field(ge=0.0, le=1.0)  # 置信度，未来可用于阈值判断
    user_question: str = ""              # 原始问题（用于展示和日志）
    normalized_question: str = ""        # 标准化问题（用于指标检索）
    reason: str = ""                     # 分类原因（可解释性 + 调试）
```

**每个字段的设计考量**：

1. **`intent_type`**：`Literal` 类型约束 LLM 只能输出这三个值之一，不会出现第四种分类
2. **`action`**：分离了"查询"和"解释"两种完全不同的下游流程，query 走 SQL 生成，explain 直接返回指标定义
3. **`is_bi`**：冗余字段但实用，Leader 可以快速判断 `if not is_bi: 结束`
4. **`chart_requested`**：当 action=query 但用户说了"画图"时，这个字段为 True，确保 visualization_agent 被调用
5. **`confidence`**：当前未使用，但预留了阈值判断的可能性（如 confidence < 0.5 时提示用户确认）
6. **`normalized_question`**：最核心的字段，剥离可视化指令后的纯业务问题

#### response_format 强制结构化输出的原理

```python
intent_classifier = {
    "name": "intent_classifier",
    "response_format": IntentClassifierOutput  # 关键参数
}
```

`response_format` 的作用是让 LLM 输出严格符合 Pydantic 模型的 JSON，而不是自由文本。实现原理：

1. deepagents 框架检测到 `response_format` 参数
2. 调用 LLM 的 `with_structured_output(IntentClassifierOutput)` 方法
3. LangChain 在调用 LLM 时，通过 OpenAI API 的 `response_format` 参数或工具调用模式，强制输出结构化 JSON
4. Pydantic 自动校验输出，字段类型不匹配会报错

**对比我方**：我方的 `classify_intent` 函数让 LLM 输出自由文本，然后通过字符串匹配判断 `"DataQuery"` 或 `"Other"`，存在解析失败的风险。

#### 追问补全的 14 条规则逐条解读

提示词的第 13-14 条是追问补全的核心规则：

**第 13 条：相对时间必须展开**

```
若存在 previous_resolved_context 且锚点问句里已出现明确历史时间，
本轮用户又使用"上个月/下个月/去年"等相对时间指代，
则必须以该历史时间为基准做日历推算，并把结果写进 normalized_question
```

示例：
```
锚点：部门名称为产科在2024年11月每位医生的门诊处方合格率
本轮：那么上个月的数据呢
结果：部门名称为产科在2024年10月每位医生的门诊处方合格率
```

**设计考量**：LLM 负责做日历推算（2024年11月 → 上个月 → 2024年10月），而不是用正则匹配。这比正则更灵活，能处理"去年同期"、"上上个月"等复杂表达。

**第 14 条：维度继承与合并**

```
normalized_question = 锚点中本轮未重写的维度 + 本轮新增或变更的维度
本轮明确说到的维度以本轮为准，覆盖锚点同维度
本轮未提到的维度从锚点继承
```

示例：
```
锚点：部门名称为产科在2024年11月每位医生的门诊处方合格率
本轮：那么外科呢
结果：部门名称为外科在2024年11月每位医生的门诊处方合格率
（科室维度被覆盖，时间维度被继承）
```

**设计考量**：这个规则确保追问时不需要用户重复完整条件。LLM 需要理解"维度"的概念（科室、时间、指标等都是维度）。

#### normalized_question 的生成逻辑

提示词第 10 条定义了 normalized_question 的生成规则：

1. **剥离可视化措辞**：去掉"用折线图展示"、"画成饼图"、"以图表形式"等纯可视化指令
2. **保留业务筛选条件**：保留所有科室、时间、指标名称等业务相关内容
3. **合并追问维度**：如果是追问，按照第 14 条规则合并

**输入输出示例**：

```
输入："用饼状图展示部门名称为产科在2024年11月每位医生的门诊处方合格率"
输出："部门名称为产科在2024年11月每位医生的门诊处方合格率"

输入："那么上个月的数据呢"
输出："部门名称为产科在2024年10月每位医生的门诊处方合格率"
```

**设计考量**：剥离可视化指令是因为指标检索（向量搜索）与图表类型无关。如果保留"用饼状图展示"，向量搜索的语义匹配会受到噪音干扰。

### 3.3 指标检索流程

#### 两阶段检索的设计思路

```
用户问题 → 向量检索(Top-5) → 相似度过滤(>=0.5) → LLM 二次筛选 → 构建上下文
```

**为什么不是直接让 LLM 选？**

如果让 LLM 直接从所有指标中选择，存在两个问题：

1. **指标数量过多**：业务系统可能有成百上千个指标，LLM 无法在一次调用中处理这么多候选
2. **LLM 不擅长精确检索**：LLM 擅长语义理解，但不擅长从大量候选中精确匹配

**两阶段设计的优势**：

1. **第一阶段（向量检索）**：Milvus 通过向量相似度快速召回 Top-5 候选，这个过程是毫秒级的
2. **第二阶段（LLM 筛选）**：LLM 只需要从 5 个候选中选择，大幅减少了 LLM 的认知负担，提高了选择准确率

#### 向量检索（Milvus + BGE）的技术细节

```python
# metric_service.py 行 90-124
def semantic_search(query, limit=5) -> List[MetricInfo]:
    # 1. 创建客户端连接
    client = MilvusClient(uri=MILVUS_URL, token="root:Milvus")
    openai_client = OpenAI(base_url=EMBEDDING_URL, api_key=EMBEDDING_API_KEY)

    # 2. 将查询文本转为 1024 维向量
    query_vectors = [
        vec.embedding
        for vec in openai_client.embeddings.create(input=query, model=EMBEDDING_MODEL).data
    ]

    # 3. 在 Milvus 中搜索最相似的 Top-5
    results = client.search(
        collection_name=MetricInfo.get_collection_name(),
        anns_field="ie_description_vector",  # 搜索向量字段
        data=query_vectors,
        limit=limit,
        output_fields=['ie_id', 'ie_code', 'ie_name', 'ie_unit', 'ie_description',
                       'ie_define', 'ie_complexity', 'ie_formula', 'factor_ie_ids', 'ie_sql']
    )

    # 4. 格式化返回
    formatted_results = []
    for hits in results:
        for hit in hits:
            formatted_results.append(MetricInfo(
                ie_id=hit.get("ie_id"),
                ie_name=hit.get("ie_name"),
                score=hit.score  # 相似度分数（0-1 之间）
            ))
    return formatted_results
```

**技术细节**：

1. **Embedding 模型**：BGE-large-zh-v1.5，1024 维，专为中文语义设计
2. **距离度量**：HNSW 索引 + IP（内积）度量，适合归一化后的向量
3. **搜索字段**：`ie_description_vector`（指标的描述文本转向量后的存储字段）

#### 相似度过滤（score >= 0.5）的设计考量

```python
# metric_searcher.py 行 81-87
input_metric_items = [
    InputMetricItem(
        metric_id=candidate.ie_id,
        metric_name=candidate.ie_name,
        metric_description=candidate.ie_description,
    )
    for candidate in candidates
    if candidate.score >= limit_score  # 默认 0.5
]
```

**为什么设 0.5？**

- 太低（如 0.3）：会引入大量假阳性，LLM 需要从更多候选中选择，增加出错概率
- 太高（如 0.8）：可能漏掉语义相似但表述不同的指标
- 0.5 是一个平衡点：过滤掉明显不相关的，保留可能相关的

#### LLM 二次选择的提示词设计

```python
# metric_selection.py 行 27-57
system_prompt = """
你是指标匹配助手。候选列表来自向量检索，可能存在假阳性。

## 你必须完成的判断
1) 语义是否真正匹配
2) 维度与对象是否兼容
3) 宁缺毋滥：若无真正匹配，返回空数组

## 输出要求
- 仅从候选中选择 metric_id
- actual_question 必须保留用户问题中的全部语义
- 输出格式：{"selections": [...]}
"""
```

**宁缺毋滥策略的意义**：

```
用户问题："今天的门诊量"
候选指标：
  1. 门诊处方合格率 (score=0.52)  ← 语义不完全匹配
  2. 住院人次数 (score=0.51)       ← 完全不匹配

宁缺毋滥：返回空数组，告诉 Leader 未找到匹配指标
而不是：选择 score 最高的"门诊处方合格率"作为近似结果
```

**设计考量**：在 BI 场景中，返回错误数据比返回无数据更危险。宁缺毋滥确保用户不会被误导。

#### 复合指标展开的递归逻辑

```python
# metric_searcher.py 行 167-213
def build_metric_context(metric_info: MetricInfo) -> MetricContext:
    if metric_info.ie_complexity == "单一指标":
        return MetricContext(
            metric_type="single",
            metric_sql=metric_info.ie_sql,
            sub_metrics=[],
            table_full_names=[extract_table_name(metric_info.ie_sql)]
        )

    elif metric_info.ie_complexity == "复合指标":
        # 查询子指标
        sub_metric_infos = list_by_ie_ids(metric_info.factor_ie_ids)
        sub_metric_contexts = []
        table_full_names = []

        for sub_metric_info in sub_metric_infos:
            table_full_names.append(extract_table_name(sub_metric_info.ie_sql))
            sub_metric_contexts.append(
                MetricContext(
                    metric_name=sub_metric_info.ie_name,
                    metric_sql=sub_metric_info.ie_sql,
                    sub_metrics=[],  # 子指标一定是单一指标
                    table_full_names=[extract_table_name(sub_metric_info.ie_sql)]
                )
            )

        return MetricContext(
            metric_type="composite",
            calc_expression=metric_info.ie_formula,  # 如 "A / B * 100"
            sub_metrics=sub_metric_contexts,
            table_full_names=list(set(table_full_names))  # 合并去重
        )
```

**限制：不支持嵌套复合指标**（代码行 186-187 的注释）

当前代码中 `sub_metrics` 始终设为空列表，没有递归调用 `build_metric_context`。这意味着：

```
支持的：复合指标 → 单一指标 + 单一指标
不支持：复合指标 → 复合指标(→ 单一指标 + 单一指标) + 单一指标
```

**为什么有这个限制？** 避免无限递归和过深的查询嵌套。实际业务中两层复合指标已经足够。

### 3.4 表结构获取流程

#### 从指标 SQL 提取表名

```python
# sql_utils.py
def extract_table_name(sql_text: str) -> str:
    pattern = r"FROM\s+([\w\.]+)(?:\s|$|WHERE|JOIN|ORDER|GROUP|HAVING|LIMIT|UNION)"
    match = re.search(pattern, sql_text.upper())
    if match:
        return match.group(1)  # 返回 SCHEMA.TABLE_NAME
    return None
```

**设计思路**：

- 指标 SQL 的格式通常是 `SELECT ... FROM SCHEMA.TABLE_NAME WHERE ...`
- 正则提取 `FROM` 后面的 `SCHEMA.TABLE_NAME` 部分
- 支持表名中包含点号（Oracle 的 `SCHEMA.TABLE` 格式）

**局限性**：

- 只提取第一个 `FROM` 后的表名，不支持 `JOIN` 多表的场景
- 不支持子查询中的表名提取

#### Milvus 精确查询表元数据

```python
# table_service.py
def get_table_info(table_schema: str, table_name: str) -> TableInfo:
    filter_expr = f'table_schema == "{table_schema}" and name == "{table_name}"'
    rows = client.query(
        collection_name=TableInfo.get_collection_name(),
        filter=filter_expr,
        output_fields=["id", "table_schema", "name", "description"],
    )
    return TableInfo(...)
```

**设计思路**：这里用 Milvus 的标量过滤（而非向量检索），因为表名是精确匹配，不需要模糊搜索。

#### 批量查询列信息

```python
# column_service.py
def query_by_table_ids(table_ids: List[str]) -> List[ColumnInfo]:
    results = client.query(
        collection_name=ColumnInfo.get_collection_name(),
        filter=f"table_id in {table_ids}",
        output_fields=['id', 'table_id', 'is_pk', 'name', 'description', 'data_type', 'length']
    )
    return [ColumnInfo(...) for result in results]
```

**设计思路**：一次查询所有表的所有列，避免 N+1 查询问题。然后在 Python 端按 `table_id` 分组。

#### data_type 字段的重要性

`data_type` 字段在 SQL 生成时起关键约束作用：

- **字符型**（VARCHAR, CHAR）：不能用 SUM/AVG，不能用日期函数
- **数值型**（NUMBER, INTEGER）：可参与 SUM/AVG/MAX/MIN
- **日期型**（DATE, TIMESTAMP）：可用 TO_CHAR、TRUNC 等日期函数

sql_agent 的系统提示词明确要求：

```
必须严格遵守 data_type 选择 SQL 函数：
- 字符型列：按字符语义处理
- 数值型列：可参与 SUM/AVG/MAX/MIN
- 日期型列：可使用日期函数
```

### 3.5 SQL 生成流程

#### 强制 5 步流程的设计思路

sql_agent 的系统提示词定义了严格的 5 步流程：

```
Step 0: 每轮先检查 skill（必须执行）
Step 1: 调用 search_schemas_tool 获取精简表结构
Step 2: 基于 Skills 规则 + 精简列生成 SQL
Step 2.5: Skills 自检（对照规则清单检查 SQL）
Step 3: 调用 valid_sql_tool 校验只读
Step 4: 调用 execute_sql_tool 执行（硬约束：最多 1 次）
```

**为什么需要如此严格的流程？**

1. **防止 LLM 跳步**：LLM 有时倾向于"直接生成 SQL"，跳过 Skills 读取和列筛选，导致不符合业务规则
2. **防止重复执行**：Step 4 的"最多调用 1 次"约束防止 LLM 执行失败后反复修改条件重试
3. **强制自检**：Step 2.5 让 LLM 生成 SQL 后对照 Skills 规则自检一遍，提高准确率

#### Step 0：Skills 读取（为什么必须先读？）

```
Step 0. 每轮先检查 skill（必须执行）
- 必须先读取并检查 sql-business-rules skill 的当前规则
- 如果 skill 内容与本轮问题相关，必须遵守
```

**为什么 Step 0 必须在 Step 1 之前？**

Skills 可能影响列筛选逻辑。例如，Skills 规则说"statistic_time 必须使用 YYYYMMDD 格式"，如果 LLM 先筛选了列再读 Skills，可能会忽略这个约束。

#### Step 1：search_schemas_tool 列筛选（两步 LLM 筛选的设计）

```python
# sql_agent.py
@tool("search_schemas_tool")
def search_schemas_tool(runtime, normalized_question):
    # 1. 从 Store 读取完整表结构
    metric_contexts = get_context(runtime, ContextKey.metric_context)
    table_contexts = get_context(runtime, ContextKey.tables_context)

    # 2. 对每个表，用 LLM 精简列
    for table_context in table_contexts:
        class KeepContextTexts(BaseModel):
            keep_context_text_names: List[str]
            reason: str

        with_structured = llm.with_structured_output(KeepContextTexts)
        result = with_structured.invoke([
            SystemMessage(content=column_system_prompt),
            HumanMessage(content=f"normalized_question:\n{normalized_question}\n"
                                 f"column_texts:\n{[col.model_dump() for col in table_context.column_contexts]}")
        ])

        # 3. 过滤列
        table_context.column_contexts = [
            col for col in table_context.column_contexts
            if col.name in result.keep_context_text_names
        ]

    # 4. 写回精简后的表结构
    put_context(runtime, ContextKey.zip_table_context, ...)
```

**两步列筛选的设计**：

- 第一步：metric_searcher 获取完整表结构（所有列）
- 第二步：sql_agent 根据具体问题精简列（只保留相关列）

**为什么需要两步？**

- 完整表结构可能有 50+ 列，一次性发给 LLM 会浪费 token
- 精简后的表结构可能只有 10 列，LLM 生成 SQL 更准确

#### Step 2：SQL 生成（白名单 + data_type 约束）

```
Step 2. 基于筛选列生成 SQL
- 物理列名白名单：只能使用 table_contexts[].column_contexts[].name 中出现的列名
- 必须严格遵守 data_type 选择 SQL 函数
- 禁止 CTE（WITH ... AS）
- Oracle 别名长度 <= 30 字节
```

**白名单机制**：LLM 只能使用 `keep_context_text_names` 中列出的列名。如果 LLM 尝试使用不在白名单中的列名，SQL 校验会失败。

**Oracle 方言的特殊约束**：

1. **禁止 CTE**：Oracle 对 CTE 的支持有限，且可能导致性能问题
2. **列别名 30 字节限制**：Oracle 12c 之前的版本限制列别名最多 30 字节（中文占 3 字节，即最多 10 个中文字符）

**列别名 30 字节限制的由来**：

Oracle 数据库在 12c 之前的版本中，标识符（包括列别名）的最大长度为 30 字节。中文在 Oracle 的 AL32UTF8 编码中占 3 字节，所以中文字符的列别名最多 10 个字。即使 12c 支持了更长的标识符，为了兼容旧版本，海泰的系统提示词中仍然限制 30 字节。

#### Step 2.5：Skills 自检（为什么不直接生成？）

```
Step 2.5. 环境规则辅助对齐
- 生成首版 SQL 后，按 Step 0 的规则清单自检
- 若不一致，重写 SQL（最多 1 次）
```

**设计思路**：LLM 不是先生成完美 SQL 的，而是先生成一个"草稿"，然后对照规则自检。这种"生成-检查-修正"的两阶段方法比一次性生成更准确。

**为什么限制最多 1 次？** 防止 LLM 陷入无限修改循环。

#### Step 3：SQL 校验

```python
# sql_utils.py
def valid_select_sql(sql_text: str):
    lower_sql = sql_text.lower()
    forbidden = (" drop ", " truncate ", " delete ", " update ", " alter ", " insert ")
    if any(token in f" {lower_sql} " for token in forbidden):
        raise Exception("仅允许只读查询 SQL（SELECT）")
    if not lower_sql.startswith("select"):
        raise Exception("仅支持 SELECT 查询")
```

**设计思路**：简单的关键词黑名单，确保 SQL 只能是 SELECT 语句。注意 `f" {lower_sql} "` 在前后加了空格，是为了避免误判包含 "select" 子字符串的列名（如 `is_selected`）。

#### Step 4：执行（硬约束单次执行）

```python
@tool("execute_sql_tool")
def execute_sql_tool(sql_text: str, runtime, limit=500):
    sql_text.removesuffix(";")
    columns, rows = execute_select(sql_text, limit=limit)

    sql_execute_result = SqlExecuteResultContext(
        sql_text=sql_text,
        columns=columns,
        rows=rows
    )
    put_context(runtime, ContextKey.sql_execute, sql_execute_result.model_dump())
    return {"ok": True, "message": "获取sql的执行结果成功"}
```

```python
# sql_utils.py
def execute_select(sql_text: str, limit: int = 100):
    valid_select_sql(sql_text)
    engine = build_oracle_engine()
    # Oracle 分页：外层包 ROWNUM 限制
    wrapped_sql = f"SELECT * FROM ({sql_text}) t WHERE ROWNUM <= {limit}"
    with engine.connect() as conn:
        execute_result = conn.execute(text(wrapped_sql))
        columns = list(execute_result.keys())
        rows = [dict(zip(columns, row)) for row in execute_result.fetchall()]
    return columns, rows
```

**硬约束单次执行的设计**：系统提示词明确规定 `execute_sql_tool 最多调用 1 次`，成功后立即返回，不得再调用任何工具。

**为什么限制单次？**

1. **安全性**：防止 LLM 反复执行不同 SQL，消耗数据库资源
2. **确定性**：确保每次用户查询最多执行一条 SQL
3. **成本控制**：减少数据库连接时间

**对比我方**：我方的 `execute_sql` 节点允许 `self_heal` 修复后重新执行，最多执行 2 次（原始 SQL + 修复后的 SQL）。

### 3.6 可视化生成流程

#### ECharts option 生成的设计思路

visualization_agent 不使用规则推断图表类型，而是让 LLM 直接生成完整的 ECharts option JSON：

```
1. 从 Store 读取 sql_execute（columns + rows）
2. 读取 Skills（chart-describe/SKILL.md + reference/*.md）
3. LLM 生成完整的 ECharts option JSON
4. JSON 自愈（如果需要）
5. 写入 Store（visualization_context）
```

**为什么让 LLM 生成而不是规则推断？**

1. **灵活性**：LLM 可以根据数据特征选择最合适的图表类型和样式
2. **自定义性**：通过 Skills 可以调整图表风格（如配色、字体大小）
3. **上下文理解**：LLM 可以理解"同比增长率"更适合用折线图而非饼图

#### JSON 自愈机制（补全右括号）

```python
# visualization_agent.py 行 37-57
if isinstance(echarts_option, str):
    try:
        loaded = json.loads(echarts_option)
    except json.JSONDecodeError as e:
        open_count = echarts_option.count("{")
        close_count = echarts_option.count("}")
        if open_count > close_count:
            repaired = echarts_option + ("}" * (open_count - close_count))
            try:
                loaded = json.loads(repaired)
                logger.warning("echarts_option JSON 缺失右花括号，已自动补齐")
            except json.JSONDecodeError:
                return {"ok": False, "message": f"echarts_option 不是合法 JSON: {e}"}
```

**设计考量**：LLM 输出 ECharts option 时可能被 max_tokens 截断，导致 JSON 不完整。最常见的截断模式是缺失右花括号。自愈机制通过统计左右括号数量差异来补全。

**局限性**：只处理缺失右括号的情况，无法修复其他类型的 JSON 错误（如缺失引号、键名错误等）。

#### Skills 分层设计（skill.md + reference/*.md）

```
skills/chart-describe/
├── SKILL.md          # 通用规则（所有图表类型共享）
└── reference/
    ├── bar.md        # 柱状图专属配置
    ├── line.md       # 折线图专属配置
    └── pie.md        # 饼图专属配置
```

**分层设计思路**：

1. `SKILL.md` 定义通用规则（如 toolbox 配置、数据映射规则）
2. `reference/*.md` 定义每种图表类型的专属配置（如 xAxis/yAxis/series 结构）
3. LLM 先读取 SKILL.md 了解通用规则，然后根据数据特征选择读取某个 reference 文件

#### 三种图表类型的模板差异

| 图表类型 | xAxis | yAxis | series | 特殊要求 |
|---------|-------|-------|--------|---------|
| 柱状图 | type: "category" | type: "value" | type: "bar" | grid 留白 |
| 折线图 | type: "category" | type: "value" | type: "line" | smooth: true |
| 饼图 | 无 | 无 | type: "pie" | radius: ["40%", "70%"] |

### 3.7 Leader 协调流程

#### prompt.py 的 5 个 section 逐条解读

Leader 的系统提示词分为 5 个 section：

**Section 1：上下文来源**

```
一、上下文来源
- metric_context：指标上下文
- tables_context：表结构上下文
- zip_table_context：精简表结构
- sql_context：SQL 文本
- sql_execute：执行结果
- visualization_context：图表配置
- intent_history：历史意图
```

**设计考量**：告诉 Leader 所有可用的 ContextKey，让它在不同 stage 知道可以从 Store 读取什么数据。

**Section 2：stage 迁移**

```
二、stage 迁移
intent → metric_search → metric_explain → generate_sql → execute_sql → visual → final

硬规则：
- 仅可按顺序前进，禁止跨越关键阶段
- 上游失败（ok=false）必须直接进入 final(failed)
- intent=life 时直接进入 final
- action=explain 时在 metric_explain 后进入 final
```

**设计考量**：严格的状态机，防止 LLM 跳过关键步骤或在失败时继续执行。

**Section 3：固定流程**

```
三、固定流程
Step 1: 调用 intent_classifier → 获取 intent_type 和 action
Step 2: 若 intent_type=life → 结束
Step 3: 调用 metric_searcher → 获取指标和表结构
Step 4: 若 action=explain → 解释指标 → 结束
Step 5: 调用 sql_agent → 生成并执行 SQL
Step 6: 判断结果是否为空
Step 7: 调用 visualization_agent → 生成图表
```

**设计考量**：每个 step 对应一个子代理调用，顺序严格，不可调换。

**Section 4：多轮规则**

```
四、多轮规则
- 追问时可复用上下文（从 Store 读取）
- 新指标需重新检索（不缓存旧指标）
- 每轮必须重新识别意图
```

**Section 5：输出要求**

```
五、输出要求
- 自然语言结论
- 有图则包含 ECHARTS_OPTION
- 失败时给出具体原因
```

---

## 第四部分：数据初始化流程

### 4.1 指标同步（init_metrics）

**etl.py 行 1-70** 的完整流程：

```
┌──────────────────────────────────────────────────────────────┐
│                    init_metrics() 流程                        │
└──────────────────────────────────────────────────────────────┘

  1. 清空 Milvus 指标 Collection
     drop_metric_info()
     │
     ▼
  2. 从业务系统 REST API 获取指标列表
     POST http://{METRIC_URI}/svc/ies/ie-maintenance/list_ie_caliber
     │
     │ 返回：[{ieId, ieName, ieComplexityCode, ieSql, factorInfoList, ...}]
     ▼
  3. 从 MongoDB 获取指标详情
     POST http://{METRIC_URI}/svc/api/ieinfo/ie-info-targets
     │
     │ 返回：[{ieId, ieDescription, ieUnit, ieDefine, ...}]
     ▼
  4. 合并数据并构建 MetricInfo
     for simple_ie_info in ie_simple_list:
         metric_info = MetricInfo(
             ie_id=simple_ie_info["ieId"],
             ie_name=simple_ie_info["ieName"],
             ie_complexity="单一指标" if code=="1" else "复合指标",
             factor_ie_ids=[factor["ieId"] for factor in factorInfoList],
             ie_sql=remove_aggregate_functions(simple_ie_info["ieSql"]),
             ...
         )
     │
     ▼
  5. 写入 Milvus（自动 Embedding）
     add_metric_info(metric_info)
     │
     │ Milvus 内置 Embedding Function 自动调用：
     │ ie_description → ie_description_vector (1024维)
     ▼
  完成
```

**remove_aggregate_functions 的设计**：

```python
def remove_aggregate_functions(sql: str) -> str:
    # 移除 SQL 中的聚合函数（SUM, COUNT, AVG 等）
    # 因为指标 SQL 模板中的聚合函数会干扰表名提取
    # 例如：SELECT SUM(amount) FROM t → SELECT amount FROM t
```

**为什么需要移除聚合函数？** 因为 `extract_table_name` 函数用正则从 `FROM` 子句提取表名，聚合函数不影响表名提取，但保留聚合函数可能导致 SQL 生成时 LLM 照搬模板而产生错误的 SQL。

### 4.2 表结构同步（init_tables）

**etl.py 行 70-148** 的完整流程：

```
┌──────────────────────────────────────────────────────────────┐
│                    init_tables() 流程                         │
└──────────────────────────────────────────────────────────────┘

  1. 清空 Milvus 表/列 Collection
     drop_table_info() + drop_column_info()
     │
     ▼
  2. 从业务系统获取指标 SQL，提取表名
     for ie_simple in ie_simple_list:
         sql = ie_simple.get("ieSql", "")
         full_table_str = extract_table_name(sql)
         # 结果如 "HIS_DATA.PRESCRIPTION"
     │
     ▼
  3. 连接 MongoDB
     client = MongoClient(MONGODB_URI)
     db = client["HTDATALAKE"]
     dataset = db["dataset"]         # 表元数据
     dataset_attr = db["dataset_attr"]  # 列元数据
     │
     ▼
  4. 构建 TableInfo 并写入 Milvus
     for table_info in table_infos:
         add_table_info(table_info)
         │
         ▼
     5. 查询列信息并写入 Milvus
        for attr in dataset_attr.find({"dataset_code": table_info.name.lower()}):
            add_column_info(ColumnInfo(
                id=uuid.uuid4().hex,
                table_id=table_info.id,
                name=attr["column_name"],
                description=attr["column_comment"],
                data_type=attr["data_type"],  # 关键字段
                ...
            ))
```

### 4.3 数据源关系图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        数据源关系图                                  │
└─────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐                    ┌──────────────────┐
  │ 业务系统 REST API │                    │     MongoDB      │
  │ (METRIC_URI)     │                    │ (HTDATALAKE)     │
  │                  │                    │                  │
  │ /svc/ies/ie-     │                    │ dataset          │ ← 表元数据
  │ maintenance/     │                    │ dataset_attr     │ ← 列元数据
  │ list_ie_caliber  │ ← 指标列表         │                  │
  │                  │                    └────────┬─────────┘
  │ /svc/api/ieinfo/ │                             │
  │ ie-info-targets  │ ← 指标详情                   │
  └────────┬─────────┘                             │
           │                                       │
           │         init_metrics()                │ init_tables()
           │              │                        │      │
           ▼              ▼                        ▼      ▼
  ┌──────────────────────────────────────────────────────────┐
  │                      Milvus                              │
  │                                                          │
  │  MetricInfo Collection                                   │
  │  ├── ie_id (PK)                                         │
  │  ├── ie_name                                            │
  │  ├── ie_description → ie_description_vector (1024维)    │
  │  ├── ie_sql                                             │
  │  ├── ie_complexity                                      │
  │  └── factor_ie_ids                                      │
  │                                                          │
  │  TableInfo Collection                                    │
  │  ├── id (PK)                                            │
  │  ├── table_schema + name                                │
  │  └── description                                        │
  │                                                          │
  │  ColumnInfo Collection                                   │
  │  ├── id (PK)                                            │
  │  ├── table_id (FK → TableInfo)                          │
  │  ├── name + data_type + description                     │
  │  └── is_pk + length                                     │
  └──────────────────────────────────────────────────────────┘
                         │
                         │ 查询时读取
                         ▼
  ┌──────────────────────────────────────────────────────────┐
  │                      Oracle                              │
  │                                                          │
  │  HIS_DATA.PRESCRIPTION                                   │
  │  HIS_DATA.OUTPATIENT                                     │
  │  ... (业务数据表)                                         │
  │                                                          │
  │  SQL 查询的实际执行目标                                    │
  └──────────────────────────────────────────────────────────┘
```

---

## 第五部分：Service 层深度解析

### 5.1 metric_service.py

#### Milvus Collection 设计

**MetricInfo Collection 的字段设计**（metric_service.py 行 19-66）：

```python
fields = [
    FieldSchema(name="ie_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
    FieldSchema(name="ie_code", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="ie_name", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="ie_unit", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="ie_description", dtype=DataType.VARCHAR, max_length=1024),
    FieldSchema(name="ie_description_vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
    FieldSchema(name="ie_define", dtype=DataType.VARCHAR, max_length=1024),
    FieldSchema(name="ie_complexity", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="ie_formula", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="factor_ie_ids", dtype=DataType.VARCHAR, max_length=1024),
    FieldSchema(name="ie_sql", dtype=DataType.VARCHAR, max_length=2048),
]
```

**设计特点**：

1. **标量字段 + 向量字段**：`ie_description` 是原始文本（标量），`ie_description_vector` 是对应的向量（1024 维浮点数组）
2. **ie_id 为主键**：使用业务系统的指标 ID 作为 Milvus 主键，避免重复插入
3. **factor_ie_ids 为 VARCHAR**：存储 JSON 格式的 ID 列表，而非 Milvus Array 类型（因为 Milvus 的 Array 字段有长度限制）

#### 内置 Embedding Function 的原理

```python
collection_schema.add_function(Function(
    name="tei_func",
    function_type=FunctionType.TEXTEMBEDDING,
    input_field_names=["ie_description"],
    output_field_names=["ie_description_vector"],
    params={
        "provider": "openai",
        "model_name": EMBEDDING_MODEL,
    }
))
```

**工作原理**：

1. `FunctionType.TEXTEMBEDDING` 告诉 Milvus 这是一个文本嵌入函数
2. `input_field_names=["ie_description"]` 指定输入字段
3. `output_field_names=["ie_description_vector"]` 指定输出字段
4. 当调用 `client.insert()` 插入数据时，Milvus 自动：
   - 读取 `ie_description` 字段的值
   - 调用配置的 OpenAI Embedding API
   - 将返回的向量写入 `ie_description_vector` 字段

**优势**：开发者无需手动调用 Embedding API，Milvus 在数据写入时自动处理。

#### HNSW 索引 + IP 度量

```python
index_params = client.prepare_index_params()
index_params.add_index(
    field_name="ie_description_vector",
    index_type="HNSW",
    metric_type="IP",       # 内积
    params={"M": 16, "efConstruction": 256}
)
```

**为什么选 HNSW？**

- HNSW（Hierarchical Navigable Small World）是当前最主流的近似最近邻（ANN）索引
- 查询速度快（毫秒级），召回率高（>95%）
- 适合中等规模数据（万级到百万级）

**为什么选 IP（内积）？**

- BGE 模型输出的向量是归一化的，内积等价于余弦相似度
- IP 的计算速度比 COSINE 快

### 5.2 table_service.py

**精确查询 vs 向量检索**：

table_service.py 提供了两种查询方式：

1. `get_table_info(table_schema, table_name)`：精确查询，使用 Milvus 的标量过滤
2. `semantic_search(query, limit)`：向量检索，搜索表描述

**设计思路**：在指标检索流程中，表名从指标 SQL 中提取（精确匹配），所以使用精确查询。但如果用户直接搜索"处方相关的表"，则使用向量检索。

**get_table_info 的 filter_expr 设计**：

```python
filter_expr = f'table_schema == "{table_schema}" and name == "{table_name}"'
```

使用 `AND` 条件精确匹配 schema 和表名，确保不会匹配到同名不同 schema 的表。

### 5.3 column_service.py

**按 table_id 批量查询的设计**：

```python
def query_by_table_ids(table_ids: List[str]) -> List[ColumnInfo]:
    filter = f"table_id in {table_ids}"
    results = client.query(...)
    return [ColumnInfo(...) for result in results]
```

使用 Milvus 的 `IN` 操作符一次查询多个 table_id 的所有列，避免 N+1 查询。

**semantic_search_with_table 的 filter 组合**：

```python
def semantic_search_with_table(query, table_id, limit):
    filter = f"table_id == '{table_id}'"
    # 在指定表的列中搜索
    results = client.search(
        filter=filter,  # 先过滤表
        data=query_vectors,  # 再向量搜索
        ...
    )
```

先通过标量过滤缩小范围（只搜索指定表的列），再进行向量检索。

### 5.4 Model2Schema 基类设计

```python
# model.py
class Model2Schema(BaseModel, ABC):
    """模型转 Milvus Schema 基类"""

    @classmethod
    def get_collection_name(cls):
        return cls.__name__  # 类名即 Collection 名

    @abstractmethod
    def get_schema(self):
        raise NotImplementedError()
```

**设计思路**：

1. **Pydantic BaseModel**：提供数据校验、序列化（`model_dump()`）、反序列化（`model_validate()`）
2. **ABC 抽象方法**：强制子类必须实现 `get_schema()`，确保每个模型都有对应的 Milvus Schema
3. **类名即 Collection 名**：`MetricInfo.get_collection_name()` 返回 `"MetricInfo"`，简化了命名管理

**为什么用 ABC + get_schema 抽象方法？**

因为 Pydantic 模型定义的是 Python 层面的数据结构，而 Milvus 需要的是数据库层面的 Schema（字段类型、索引等）。两者之间需要手动映射，所以用抽象方法强制每个子类提供自己的映射逻辑。

---

## 第六部分：部署架构

### 6.1 Docker 构建流程

**Dockerfile 分析**（47 行）：

```dockerfile
# 基础镜像：uv + Python 3.12
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# 换阿里云源（国内加速）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# 安装 Oracle Instant Client 依赖
RUN apt-get update && apt-get install -y libaio1 libaio-dev unzip

# 复制 Oracle Instant Client
COPY instantclient-basic-linux.x64-19.30.0.0.0dbru.zip /tmp/
RUN unzip /tmp/instantclient-basic-linux.x64-19.30.0.0.0dbru.zip -d /app/
ENV LD_LIBRARY_PATH="/app/instantclient_19_30"

# 安装 Python 依赖
COPY pyproject.toml uv.lock ./
RUN uv venv && uv sync --frozen --no-dev

# 复制源码
COPY . /app

# 暴露端口
EXPOSE 8123

# 启动命令
CMD [".venv/bin/python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8123"]
```

**关键设计点**：

1. **uv 而非 pip**：uv 是 Rust 实现的高速包管理器，安装依赖比 pip 快 10-100 倍
2. **Oracle Instant Client**：必须安装 Oracle 客户端库才能通过 oracledb 驱动连接 Oracle 数据库
3. **--frozen --no-dev**：`--frozen` 锁定版本，`--no-dev` 不安装开发依赖，减小镜像体积

### 6.2 运行时架构

**docker-entrypoint.sh**（12 行）：

```bash
#!/bin/bash
set -e

# 初始化 skills 目录
if [ ! -d /home/skills ] || [ -z "$(ls -A /home/skills 2>/dev/null)" ]; then
    mkdir -p /home/skills
    if [ -d /app/skills ]; then
        cp -r /app/skills/* /home/skills/
    fi
fi

exec "$@"
```

**设计思路**：

1. Skills 目录 `/home/skills` 通过 Docker Volume 挂载
2. 首次启动时从 `/app/skills`（镜像内）复制到 `/home/skills`（挂载卷）
3. 后续启动直接使用挂载卷中的 Skills（可热更新）
4. `exec "$@"` 确保启动命令接替 entrypoint 进程，正确接收信号

**连接池配置**：

```python
# main.py
async_pool = AsyncConnectionPool(
    POSTGRES_URI,
    min_size=1,      # 最小连接数
    max_size=10,     # 最大连接数
)
```

**资源清理**：

```python
try:
    yield  # 应用运行
finally:
    await async_pool.close()  # 关闭异步连接池
    sync_pool.close()         # 关闭同步连接池
```

---

## 第七部分：完整时序图

### 7.1 首次查询时序图

```
用户                  前端              FastAPI         Leader          intent          metric          sql             visualization
 │                    │                │              Agent          classifier       searcher        agent             agent
 │                    │                │              │               │               │               │               │
 │ "产科门诊处方       │                │              │               │               │               │               │
 │  合格率,折线图"    │                │              │               │               │               │               │
 │──────────────────▶│                │              │               │               │               │               │
 │                    │ POST /         │              │               │               │               │               │
 │                    │ {message,      │              │               │               │               │               │
 │                    │  thread_id,    │              │               │               │               │               │
 │                    │  context}      │              │               │               │               │               │
 │                    │───────────────▶│              │               │               │               │               │
 │                    │                │ stream()     │               │               │               │               │
 │                    │                │─────────────▶│               │               │               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 1. now_time()  │               │               │               │
 │                    │                │              │──────────┐    │               │               │               │
 │                    │                │              │◀─────────┘    │               │               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 2. get_intent_history()          │               │               │
 │                    │                │              │──────────┐    │               │               │               │
 │                    │                │              │◀─────────┘    │               │               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 3. task(intent_classifier)      │               │               │
 │                    │                │              │──────────────────────────────▶│               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │ LLM 分类      │               │               │
 │                    │                │              │               │ intent_type=  │               │               │
 │                    │                │              │               │ "bi_query"    │               │               │
 │                    │                │              │               │ action="chart"│               │               │
 │                    │                │              │               │ normalized_   │               │               │
 │                    │                │              │               │ question="产科│               │               │
 │                    │                │              │               │ 门诊处方合格率"│               │               │
 │                    │                │              │◀──────────────────────────────│               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 4. append_intent_history()      │               │               │
 │                    │                │              │──────────┐    │               │               │               │
 │                    │                │              │◀─────────┘    │               │               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 5. task(metric_searcher)        │               │               │
 │                    │                │              │────────────────────────────────────────────▶│               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │ a. search_    │               │
 │                    │                │              │               │               │ metrics_tool  │               │
 │                    │                │              │               │               │   Milvus 向量 │               │
 │                    │                │              │               │               │   检索 Top-5  │               │
 │                    │                │              │               │               │   LLM 二次筛选│               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │ b. search_    │               │
 │                    │                │              │               │               │ table_schemas │               │
 │                    │                │              │               │               │   Milvus 查表 │               │
 │                    │                │              │               │               │   Milvus 查列 │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │ Store:        │               │
 │                    │                │              │               │               │ metric_context│               │
 │                    │                │              │               │               │ tables_context│               │
 │                    │                │              │◀────────────────────────────────────────────│               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 6. task(sql_agent)              │               │               │
 │                    │                │              │────────────────────────────────────────────────────────────▶│
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ a. read Skills│
 │                    │                │              │               │               │               │ (SKILL.md)   │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ b. search_   │
 │                    │                │              │               │               │               │ schemas_tool │
 │                    │                │              │               │               │               │ (列筛选)     │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ c. 生成 SQL  │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ d. Skills    │
 │                    │                │              │               │               │               │ 自检         │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ e. valid_sql │
 │                    │                │              │               │               │               │ _tool (校验) │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ f. execute_  │
 │                    │                │              │               │               │               │ sql_tool     │
 │                    │                │              │               │               │               │ (Oracle)     │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │ Store:       │
 │                    │                │              │               │               │               │ sql_context  │
 │                    │                │              │               │               │               │ sql_execute  │
 │                    │                │              │◀────────────────────────────────────────────────────────────│
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 7. task(visualization_agent)    │               │               │
 │                    │                │              │────────────────────────────────────────────────────────────────────────▶│
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │               │ a. get_sql_
 │                    │                │              │               │               │               │               │ execute_  │
 │                    │                │              │               │               │               │               │ result() │
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │               │ b. read
 │                    │                │              │               │               │               │               │ Skills
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │               │ c. 生成
 │                    │                │              │               │               │               │               │ ECharts
 │                    │                │              │               │               │               │               │ option
 │                    │                │              │               │               │               │               │
 │                    │                │              │               │               │               │               │ d. save_
 │                    │                │              │               │               │               │               │ visual...
 │                    │                │              │               │               │               │               │
 │                    │                │              │◀────────────────────────────────────────────────────────────────────────│
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 8. get_context_by_key           │               │               │
 │                    │                │              │    (visualization_context)      │               │               │
 │                    │                │              │               │               │               │               │
 │                    │                │              │ 9. 构建最终回复                  │               │               │
 │                    │ SSE: final     │              │               │               │               │               │
 │                    │ {text,         │◀───────────────│               │               │               │               │
 │                    │  ECHARTS_      │              │               │               │               │               │
 │                    │  OPTION}       │              │               │               │               │               │
 │                    │◀───────────────│              │               │               │               │               │
 │ 渲染图表           │                │              │               │               │               │               │
 │◀──────────────────│                │              │               │               │               │               │
```

### 7.2 追问时序图

```
用户                     Leader Agent                                    intent_classifier
 │                          │                                                    │
 │ 第一轮:                   │                                                    │
 │ "产科门诊处方合格率"      │                                                    │
 │─────────────────────────▶│                                                    │
 │                          │ task(intent_classifier)                             │
 │                          │───────────────────────────────────────────────────▶│
 │                          │                    normalized_question:            │
 │                          │                    "产科门诊处方合格率"              │
 │                          │◀───────────────────────────────────────────────────│
 │                          │                                                    │
 │                          │ append_intent_history()                            │
 │                          │ Store: intent_history ← [{nq: "产科门诊处方合格率"}]│
 │                          │                                                    │
 │                          │ task(metric_searcher) → task(sql_agent) → ...      │
 │                          │                                                    │
 │◀─────────────────────────│ 返回结果                                           │
 │                          │                                                    │
 │ 第二轮:                   │                                                    │
 │ "那么上个月呢"            │                                                    │
 │─────────────────────────▶│                                                    │
 │                          │ now_time() → 2026-05-10                           │
 │                          │ get_intent_history()                               │
 │                          │   → [{nq: "产科门诊处方合格率"}]                    │
 │                          │                                                    │
 │                          │ task(intent_classifier)                            │
 │                          │   description: "请对以下用户问题进行意图识别        │
 │                          │    并结合历史上下文进行追问补全：                   │
 │                          │    用户问题：那么上个月呢                           │
 │                          │    历史上下文：产科门诊处方合格率"                   │
 │                          │───────────────────────────────────────────────────▶│
 │                          │                                                    │
 │                          │           LLM 推理:                                │
 │                          │           锚点 = "产科门诊处方合格率"               │
 │                          │           "上个月" = 相对时间                      │
 │                          │           但锚点无明确时间 → 无法推算               │
 │                          │           normalized_question =                    │
 │                          │           "产科门诊处方合格率 上个月"              │
 │                          │                                                    │
 │                          │◀───────────────────────────────────────────────────│
 │                          │                                                    │
 │                          │ append_intent_history()                            │
 │                          │ Store: intent_history ← [                          │
 │                          │   {nq: "产科门诊处方合格率"},                      │
 │                          │   {nq: "产科门诊处方合格率 上个月"}                 │
 │                          │ ]                                                  │
 │                          │                                                    │
 │                          │ task(metric_searcher) → 复用 Store 中的            │
 │                          │   metric_context / tables_context                  │
 │                          │   （如果指标没变，可跳过重新检索）                    │
 │                          │                                                    │
 │                          │ task(sql_agent) → 重新生成 SQL（时间条件变了）      │
 │                          │                                                    │
 │◀─────────────────────────│ 返回新结果                                         │
```

### 7.3 explain 时序图

```
用户                     Leader Agent              intent_classifier      metric_searcher
 │                          │                          │                       │
 │ "门诊处方合格率          │                          │                       │
 │  是什么意思"             │                          │                       │
 │─────────────────────────▶│                          │                       │
 │                          │ task(intent_classifier)   │                       │
 │                          │─────────────────────────▶│                       │
 │                          │                          │                       │
 │                          │   intent_type = "bi_query"                       │
 │                          │   action = "explain"      │                       │
 │                          │◀─────────────────────────│                       │
 │                          │                          │                       │
 │                          │ task(metric_searcher)     │                       │
 │                          │─────────────────────────────────────────────────▶│
 │                          │                          │                       │
 │                          │                          │     检索到指标:        │
 │                          │                          │     门诊处方合格率     │
 │                          │                          │     公式: A / B * 100 │
 │                          │                          │     子指标: A, B       │
 │                          │◀─────────────────────────────────────────────────│
 │                          │                          │                       │
 │                          │ 检测到 action=explain    │                       │
 │                          │ 不调用 sql_agent          │                       │
 │                          │ 不调用 visualization_agent│                       │
 │                          │                          │                       │
 │                          │ 从 Store 读取 metric_context                    │
 │                          │ 构建解释文本:            │                       │
 │                          │ "门诊处方合格率 =        │                       │
 │                          │  门诊合格处方人次数 /     │                       │
 │                          │  门诊处方人次数 * 100"    │                       │
 │                          │                          │                       │
 │◀─────────────────────────│ 返回解释                 │                       │
```

### 7.4 数据初始化时序图

```
管理员                  FastAPI              etl.py             业务系统API       MongoDB           Milvus
 │                       │                    │                     │               │               │
 │ GET /api/init-metrics │                    │                     │               │               │
 │──────────────────────▶│                    │                     │               │               │
 │                       │ init_metrics()     │                     │               │               │
 │                       │───────────────────▶│                     │               │               │
 │                       │                    │                     │               │               │
 │                       │                    │ drop_metric_info()  │               │               │
 │                       │                    │────────────────────────────────────────────────────▶│
 │                       │                    │                     │               │     清空指标   │
 │                       │                    │                     │               │     Collection │
 │                       │                    │                     │               │               │
 │                       │                    │ POST /svc/ies/ie-   │               │               │
 │                       │                    │ maintenance/list_   │               │               │
 │                       │                    │ ie_caliber          │               │               │
 │                       │                    │────────────────────▶│               │               │
 │                       │                    │◀────────────────────│               │               │
 │                       │                    │ [{ieId, ieName,     │               │               │
 │                       │                    │   ieSql, ...}]      │               │               │
 │                       │                    │                     │               │               │
 │                       │                    │ POST /svc/api/      │               │               │
 │                       │                    │ ieinfo/ie-info-     │               │               │
 │                       │                    │ targets             │               │               │
 │                       │                    │────────────────────▶│               │               │
 │                       │                    │◀────────────────────│               │               │
 │                       │                    │ [{ieId,             │               │               │
 │                       │                    │   ieDescription,    │               │               │
 │                       │                    │   ieUnit, ...}]     │               │               │
 │                       │                    │                     │               │               │
 │                       │                    │ for each metric:    │               │               │
 │                       │                    │   add_metric_info() │               │               │
 │                       │                    │────────────────────────────────────────────────────▶│
 │                       │                    │                     │               │  INSERT + 自动 │
 │                       │                    │                     │               │  Embedding     │
 │                       │                    │                     │               │               │
 │                       │◀───────────────────│                     │               │               │
 │ {"ok": true}          │                    │                     │               │               │
 │◀──────────────────────│                    │                     │               │               │
 │                       │                    │                     │               │               │
 │ GET /api/init-tables  │                    │                     │               │               │
 │──────────────────────▶│                    │                     │               │               │
 │                       │ init_tables()      │                     │               │               │
 │                       │───────────────────▶│                     │               │               │
 │                       │                    │                     │               │               │
 │                       │                    │ drop_table_info()   │               │               │
 │                       │                    │ drop_column_info()  │               │               │
 │                       │                    │────────────────────────────────────────────────────▶│
 │                       │                    │                     │               │     清空       │
 │                       │                    │                     │               │               │
 │                       │                    │ POST (获取指标SQL)   │               │               │
 │                       │                    │────────────────────▶│               │               │
 │                       │                    │◀────────────────────│               │               │
 │                       │                    │                     │               │               │
 │                       │                    │ extract_table_name()│               │               │
 │                       │                    │ 从 SQL 提取表名     │               │               │
 │                       │                    │                     │               │               │
 │                       │                    │ MongoClient()       │               │               │
 │                       │                    │─────────────────────────────────────▶│               │
 │                       │                    │ db.HTDATALAKE.      │               │               │
 │                       │                    │ dataset.find()      │               │               │
 │                       │                    │◀────────────────────────────────────│               │
 │                       │                    │                     │               │               │
 │                       │                    │ for each table:     │               │               │
 │                       │                    │   add_table_info()  │               │               │
 │                       │                    │────────────────────────────────────────────────────▶│
 │                       │                    │                     │               │               │
 │                       │                    │   dataset_attr.find │               │               │
 │                       │                    │   ({dataset_code:   │               │               │
 │                       │                    │    table_name})     │               │               │
 │                       │                    │◀────────────────────────────────────│               │
 │                       │                    │                     │               │               │
 │                       │                    │   for each column:  │               │               │
 │                       │                    │     add_column_     │               │               │
 │                       │                    │     info()          │               │               │
 │                       │                    │────────────────────────────────────────────────────▶│
 │                       │                    │                     │               │               │
 │                       │◀───────────────────│                     │               │               │
 │ {"ok": true}          │                    │                     │               │               │
 │◀──────────────────────│                    │                     │               │               │
```

---

## 第八部分：设计模式总结

### 8.1 使用的设计模式

| 设计模式 | 应用位置 | 说明 |
|---------|---------|------|
| **Leader-Worker** | leader_agent.py + 4 个子代理 | Leader 协调 Worker 执行，Worker 只负责单一职责 |
| **策略模式** | metric_selection.py 的 `_extract_json_from_text` | 多种 JSON 提取策略（直接解析 → 修复引号 → Markdown 代码块 → 外层 JSON） |
| **模板方法模式** | Model2Schema ABC | `get_schema()` 是抽象方法，子类（MetricInfo、TableInfo、ColumnInfo）各自实现 |
| **工厂方法** | `model.py` 的 `get_model()` | 集中创建 LLM 实例，统一配置 |
| **观察者模式** | SkillsObserverMiddleware | 三层拦截观察 Agent 行为 |
| **外观模式** | `create_deep_agent()` | 封装 LangGraph 的 StateGraph 构建、工具注册、Checkpointer 绑定等复杂逻辑 |
| **中介者模式** | PostgresStore | 子代理不直接通信，通过 Store 作为中介交换数据 |
| **序列化适配器** | `_to_jsonable()` | 将 Decimal/datetime/memoryview 等类型适配为 JSON 兼容类型 |
| **防御性编程** | `valid_select_sql()` | SQL 执行前强制校验只读，防止注入 |
| **自愈模式** | visualization_agent.py 的 JSON 修复 | 检测并修复 LLM 输出的不完整 JSON |
| **枚举约束** | ContextKey 枚举 | 避免键名拼写错误，集中管理状态键 |

### 8.2 架构决策记录

| 编号 | 决策 | 原因 | 替代方案 | 为什么不选替代方案 |
|------|------|------|---------|------------------|
| ADR-1 | 使用 deepagents 框架 | 封装 LangGraph 复杂度，支持多 Agent | 原生 LangGraph StateGraph | 手动构建 StateGraph + 工具注册 + 子代理委派代码量大 |
| ADR-2 | Milvus 向量检索指标 | 语义检索召回率高于关键词匹配 | pgvector | Milvus 内置 Embedding Function，且 HNSW 性能更好 |
| ADR-3 | PostgresStore 持久化状态 | 支持会话恢复和追问 | Redis | Redis 不支持 Namespace + Key-Value 的灵活查询 |
| ADR-4 | Skills 外置业务规则 | 热更新，无需改代码 | 硬编码在提示词 | 修改规则需要修改代码和重启服务 |
| ADR-5 | LLM 生成 ECharts option | 灵活自适应，支持复杂图表 | 规则推断 | 规则推断灵活性不足，无法处理复杂的数据可视化需求 |
| ADR-6 | 强制 5 步 SQL 生成流程 | 防止 LLM 跳步或违反业务规则 | 自由生成 | LLM 可能忽略业务规则或生成不安全的 SQL |
| ADR-7 | 单次执行约束 | 安全性和成本控制 | 允许重试 | LLM 可能反复执行不同 SQL 消耗资源 |
| ADR-8 | Pydantic 强制结构化输出 | 减少解析错误，类型安全 | 自由文本 + 正则解析 | 本地模型输出不稳定，解析容易出错 |
| ADR-9 | Oracle 方言兼容 | 目标业务系统是 Oracle | MySQL/PostgreSQL | 医院 HIS 系统固定使用 Oracle |
| ADR-10 | CopilotKit 前端集成 | 标准化 Agent UI，开箱即用 | 自建 SSE + Vue 组件 | 开发成本高，维护负担重 |

### 8.3 代码质量评估

#### 优点

1. **架构清晰**：Leader-Worker 模式 + Store 中介通信，职责分离做得好
2. **类型安全**：Pydantic 模型 + Literal 类型 + Enum，减少运行时错误
3. **可观测性**：SkillsObserverMiddleware 三层拦截 + 全链路日志
4. **可扩展性**：Skills 热更新 + Middleware 可插拔 + 子代理独立
5. **防御性编程**：SQL 只读校验 + JSON 自愈 + 序列化适配
6. **配置外置**：所有环境变量集中在 config.py，Skills 通过 Markdown 管理

#### 不足

1. **无多租户设计**：Context 只有 user_id 和 user_name，没有 tenant_id，不同租户的指标数据可能混淆
2. **无权限控制**：没有 RBAC 或权限校验，任何用户都能查看所有指标和执行任意 SELECT
3. **Oracle 单一数据源**：sql_utils.py 硬编码了 Oracle 连接，不支持 MySQL/PostgreSQL
4. **测试覆盖不足**：没有看到单元测试或集成测试
5. **复合指标限制**：不支持嵌套复合指标，递归结构虽然预留了但未实现
6. **表名提取不完善**：`extract_table_name` 只能提取第一个 FROM 后的表名，不支持 JOIN
7. **SQL 注入风险**：`valid_select_sql` 使用关键词黑名单，不如 AST 解析可靠
8. **错误处理粗糙**：多处使用 `Exception` 而非自定义异常，错误信息不够结构化
9. **连接管理**：每次 Milvus 操作都创建新客户端，没有连接池复用

---

## 第九部分：与我方对比

### 9.1 架构对比

| 维度 | 海泰 ChatBI | 我方 ChatBI |
|------|------------|-------------|
| **AI 框架** | deepagents（封装 LangGraph） | 原生 LangGraph StateGraph |
| **Agent 模式** | Leader-Worker 多 Agent（4 个子代理） | 单层线性管道（6 个节点函数） |
| **状态管理** | PostgresStore（持久化）+ ContextKey 枚举 | QueryState TypedDict（内存） |
| **会话持久化** | PostgreSQL Checkpointer（自动保存每步） | 无（Redis 仅缓存查询结果） |
| **子代理通信** | Store 读写（中介者模式） | 直接函数调用 + dict 返回 |
| **前端协议** | ag-ui-langgraph（标准化 Agent UI） | SSE（自定义流式协议） |
| **部署模式** | Docker 单容器 | Docker Compose 多容器 |

### 9.2 功能对比

| 维度 | 海泰 ChatBI | 我方 ChatBI |
|------|------------|-------------|
| **意图识别** | 三类型 x 四动作 + normalized_question | 二分类（DataQuery/Other） |
| **指标体系** | 预定义指标库 + 复合指标 + explain | 无指标概念 |
| **Schema 检索** | Milvus 向量检索 + LLM 二次筛选 | LLM 一次性选表选列 |
| **SQL 生成** | 强制 5 步流程 + Skills 注入 | 三次降级重试 + self_heal |
| **SQL 校验** | 关键词黑名单 | 关键词黑名单 |
| **图表生成** | LLM 生成 ECharts option + Skills 约束 | 规则推断（infer_chart_type） |
| **追问处理** | intent_history + 维度继承 + 相对时间解析 | conversation_history + 关键词替换 |
| **多数据源** | 仅 Oracle | MySQL + PostgreSQL |
| **多租户** | 无 | 完善（tenant_id 全链路） |
| **权限控制** | 无 | RBAC + JWT |
| **缓存** | 无 | Redis 精确缓存 + 语义缓存 |

### 9.3 技术差距分析

| 差距 | 严重程度 | 说明 |
|------|---------|------|
| **无向量检索** | 高 | 指标/Schema 匹配完全依赖 LLM 理解，召回率低 |
| **无指标库** | 高 | 无法预定义业务指标，无法支持复合计算 |
| **无会话持久化** | 中 | 追问体验差，无法恢复中断的对话 |
| **无 Skills 机制** | 中 | 业务规则修改需要改代码 |
| **无结构化意图输出** | 中 | LLM 输出解析不可靠，不支持 explain 场景 |
| **无 normalized_question** | 低 | 可视化指令干扰指标检索准确率 |
| **图表生成简单** | 低 | 规则推断灵活性不足 |

### 9.4 可借鉴点优先级排序

#### P0（立即实施，1-2 周）

| 借鉴点 | 工作量 | 收益 | 实施方案 |
|--------|--------|------|---------|
| **结构化意图输出** | 低 | 减少 LLM 输出解析错误 | 将 intent.py 改为 `with_structured_output(IntentOutput)`，增加 action 和 normalized_question 字段 |
| **normalized_question** | 低 | 提高指标检索准确率 | 在意图识别后剥离可视化指令，将纯净问题传给 Schema 选择 |
| **Skills 机制** | 中 | 业务规则热更新 | 创建 skills/ 目录，SQL 生成前读取 SKILL.md 并注入提示词 |

#### P1（中期规划，1-2 月）

| 借鉴点 | 工作量 | 收益 | 实施方案 |
|--------|--------|------|---------|
| **向量检索指标库** | 高 | 语义检索，召回率提升 | 引入 Milvus/pgvector，预定义业务指标，实现两阶段检索 |
| **会话状态持久化** | 中 | 追问体验改善 | 引入 PostgreSQL Checkpointer，替换内存状态管理 |
| **追问补全逻辑** | 中 | 多轮对话更智能 | 完善 intent_history 存储，实现维度继承 + 相对时间解析 |
| **LLM 生成图表** | 中 | 图表更灵活 | 将 infer_chart_type 替换为 LLM 生成 ECharts option |

#### P2（长期规划，3-6 月）

| 借鉴点 | 工作量 | 收益 | 实施方案 |
|--------|--------|------|---------|
| **多 Agent 架构** | 高 | 架构升级，扩展性强 | 评估 deepagents 框架或自建 Leader-Worker 模式 |
| **复合指标支持** | 高 | 支持复杂业务计算 | 设计指标关联关系，实现子指标自动展开 |
| **CopilotKit 集成** | 中 | 标准化前端 | 替换自建 SSE + Vue 组件为 CopilotKit |
| **Middleware 可观测** | 中 | 调试效率提升 | 在 AI 管线中添加 before/after 钩子 |

---

*分析完成时间：2026-05-10*
