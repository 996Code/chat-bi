# ChatBI 系统对比分析报告

> 对比对象：我方 ChatBI vs 海泰 ChatBI（ht-chat-bi）
> 分析日期：2026-05-09

---

## 一、技术栈对比

### 1.1 后端框架

| 维度 | 我方 ChatBI | 海泰 ChatBI |
|------|-------------|-------------|
| **Web 框架** | FastAPI | FastAPI |
| **Python 版本** | 3.12 | 3.12 |
| **包管理** | pip + requirements.txt | uv + pyproject.toml（现代化） |
| **配置管理** | Pydantic BaseSettings + .env | dotenv + os.getenv（简单） |

### 1.2 AI 框架

| 维度 | 我方 ChatBI | 海泰 ChatBI |
|------|-------------|-------------|
| **核心框架** | LangGraph（自建 StateGraph） | **deepagents + LangGraph** |
| **Agent 架构** | 单层 StateGraph + 节点函数 | **多层 Agent 协作（Leader + Sub-agents）** |
| **状态持久化** | Redis（缓存） | **PostgreSQL（Checkpoint + Store）** |
| **会话管理** | 无状态 + Redis 缓存 | **LangGraph Checkpointer（完整会话状态）** |
| **工具调用** | 直接函数调用 | **ToolRuntime + @tool 装饰器** |

### 1.3 向量数据库

| 维度 | 我方 ChatBI | 海泰 ChatBI |
|------|-------------|-------------|
| **向量数据库** | 无（关键词匹配） | **Milvus（专业向量检索）** |
| **Embedding 模型** | 无 | **BGE-large-zh-v1.5（1024 维）** |
| **语义检索** | 无 | **向量相似度 + 阈值过滤** |

### 1.4 业务数据库

| 维度 | 我方 ChatBI | 海泰 ChatBI |
|------|-------------|-------------|
| **元数据库** | MySQL | PostgreSQL + MongoDB |
| **目标数据库** | MySQL + PostgreSQL | **Oracle** |
| **数据源存储** | MySQL metadata_configs | **Milvus（指标库）+ PostgreSQL（表结构）** |

### 1.5 LLM 配置

| 维度 | 我方 ChatBI | 海泰 ChatBI |
|------|-------------|-------------|
| **LLM 提供商** | Mimo API（Qwen 系） | 自部署 vLLM（Qwen3-30B） |
| **模型** | mimo-v2.5-pro | Qwen3-30B-A3B-Instruct |
| **调用方式** | LangChain ChatOpenAI | LangChain ChatOpenAI |
| **Temperature** | 0（确定性） | 0（确定性） |
| **Max Tokens** | 2000 | 65536（更大上下文） |

### 1.6 前端技术

| 维度 | 我方 ChatBI | 海泰 ChatBI |
|------|-------------|-------------|
| **框架** | Vue3 + TypeScript | **CopilotKit（AI 前端组件库）** |
| **图表库** | ECharts | ECharts |
| **UI 组件** | Element Plus | 无明确信息 |
| **通信方式** | SSE 流式 | **ag-ui-langgraph（Agent UI 协议）** |

---

## 二、架构设计对比

### 2.1 整体架构

#### 我方架构：线性管道模式
```
用户提问 → 意图识别 → 上下文补全 → Schema选择 → SQL生成 → 执行 → 自愈 → 图表推断 → 返回
```

**特点**：
- 单层 StateGraph，节点按顺序执行
- 无嵌套 Agent，每个节点是独立函数
- 错误处理通过 try-except + fallback
- 状态通过 TypedDict 在节点间传递

#### 海泰架构：多 Agent 协作模式
```
                    ┌─────────────────┐
                    │  Leader Agent   │ ← 主协调器
                    └────────┬────────┘
                             │
        ┌──────────┬─────────┼─────────┬──────────┐
        ▼          ▼         ▼         ▼          ▼
┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────────┐
│  Intent   │ │  Metric   │ │    SQL    │ │ Visualization │
│ Classifier│ │ Searcher  │ │   Agent   │ │    Agent      │
└───────────┘ └───────────┘ └───────────┘ └───────────────┘
```

**特点**：
- **deepagents 框架**：封装了 Agent 创建、子代理委派、状态管理
- **Leader-Worker 模式**：主 Agent 协调多个子 Agent
- **统一 Store**：PostgresStore 持久化所有中间状态
- **Skills 机制**：业务规则通过 SKILL.md 动态注入

### 2.2 状态管理

#### 我方：Redis 缓存
```python
# 精确缓存 + 语义缓存
cache_key = f"query:{hash(question + datasource_id + tenant_id)}"
await redis.setex(cache_key, 3600, json.dumps(result))
```
- 仅用于查询结果缓存
- 不保存会话状态
- 无法跨轮次传递上下文（需手动传参）

#### 海泰：LangGraph Checkpointer + PostgresStore
```python
# 会话状态持久化
checkpointer = AsyncPostgresSaver(async_pool)
store = PostgresStore(sync_pool)

# 读取历史状态
store.get(namespace=(user_id, thread_id), key="metric_context")
```
- **完整会话状态持久化**
- **支持会话恢复**：中断后可继续
- **结构化状态管理**：通过 ContextKey 枚举管理所有状态类型

### 2.3 子代理通信

#### 我方：函数调用 + 参数传递
```python
# 节点函数直接调用
result = await generate_sql(question, schema_context, raw_metadata)
# 手动传递状态
return {"sql": result["sql"], "table_fixes": result["table_fixes"]}
```

#### 海泰：Store 读写 + ToolRuntime
```python
# 子代理写入 Store
put_context(runtime, ContextKey.metric_context, metric_data)

# Leader 读取 Store
metric_context = get_context(runtime, ContextKey.metric_context)

# 子代理调用通过 task 工具
task(subagent_type="sql_agent", description="...")
```

**优势**：
- 子代理只返回简短结果 `{ok, message}`
- 详细数据通过 Store 异步读写
- Leader 不需要了解子代理内部实现

---

## 三、核心功能对比

### 3.1 意图识别

#### 我方：三层分类
```
Redis 缓存 → 关键词匹配 → LLM 分类
```
- 分类：`DataQuery` / `Other`
- 简单高效，但能力有限

#### 海泰：结构化意图输出
```python
class IntentClassifierOutput(BaseModel):
    intent_type: Literal["bi_query", "chart_only", "life"]
    action: Literal["query", "explain", "chart", "other"]
    is_bi: bool
    chart_requested: bool
    confidence: float
    user_question: str
    normalized_question: str  # 标准化问题
    reason: str
```

**优势**：
- **多维度分类**：不仅判断类型，还提取标准化问题
- **追问处理**：支持历史上下文补全（relative time + 省略补全）
- **置信度**：输出 confidence 用于下游决策

### 3.2 指标检索

#### 我方：Schema 选择（两步 LLM）
```
选表 → 选列 → 构建 schema_context
```
- 基于 LLM 理解 schema 内容
- 无预定义指标库
- 依赖用户问题与表结构语义匹配

#### 海泰：指标库 + 向量检索
```python
# Milvus 向量检索
candidates = semantic_search(normalized_question, limit=5)

# LLM 二次筛选
selected = select_metrics_from_candidates(normalized_question, candidates)
```

**优势**：
- **预定义指标库**：业务指标预先定义（名称、描述、SQL、公式）
- **向量检索**：语义相似度匹配，召回率更高
- **复合指标支持**：指标可由子指标组合计算
- **指标解释**：支持 `action=explain` 查询指标定义

### 3.3 SQL 生成

#### 我方：三次降级重试
```
精选 schema → 完整 schema → 高温度简化提示词
```
- 依赖 LLM 自主理解 schema
- 表名/列名幻觉修复（模糊匹配）

#### 海泰：Skills 注入业务规则
```python
# 业务规则通过 SKILL.md 注入
skills=[get_skill_dir("sql-business-rules")]
```

**SKILL.md 示例**：
```markdown
## 时间与 `statistic_time`
1. 只要 SQL 使用了 `statistic_time` 过滤，就不要再使用 `time_dime` 条件
2. `statistic_time` 必须使用 8 位 `YYYYMMDD` 格式

## 非空规则
生成的 SQL 中，凡出现在查询列里的业务列，都必须在 `WHERE` 中拼接对应的 `IS NOT NULL` 条件
```

**优势**：
- **业务规则外置**：修改规则无需改代码
- **环境适配**：不同部署环境可使用不同规则
- **强制约束**：LLM 生成 SQL 前必须读取 Skills

### 3.4 图表生成

#### 我方：规则推断
```python
def infer_chart_type(columns, rows):
    if len(columns) == 1 and len(rows) == 1:
        return "metric"
    if len(columns) == 2 and has_time_column(columns):
        return "line"
    # ...
```
- 纯规则匹配，无 LLM 参与
- 快速但灵活性有限

#### 海泰：LLM 生成 ECharts Option
```python
# Visualization Agent 生成完整 ECharts 配置
echarts_option = generate_echarts_option(columns, rows)
save_visualization_context(echarts_option)
```

**优势**：
- **完整 ECharts 配置**：直接用于 `myChart.setOption()`
- **Skills 约束**：通过 `chart-describe/skill.md` 约束图表样式
- **自适应 JSON 修复**：自动补全缺失的右括号

---

## 四、关键差异点分析

### 4.1 deepagents 框架

海泰使用 `deepagents` 库封装 LangGraph，提供：

```python
agent = create_deep_agent(
    name="leader_agent",
    context_schema=Context,
    backend=FilesystemBackend,
    model=get_model(),
    system_prompt=leader_system_prompt,
    tools=[get_context_by_key, ...],
    subagents=[intent_classifier, metric_searcher, sql_agent, visualization_agent],
    middleware=[],
    checkpointer=checkpointer,
    store=store,
)
```

**核心特性**：
1. **子代理委派**：通过 `task` 工具自动路由到子代理
2. **状态继承**：子代理自动继承父代理的 context 和 store
3. **Middleware**：可插入中间件处理日志、监控等
4. **Backend 抽象**：支持文件系统、数据库等多种后端

### 4.2 Skills 机制

海泰通过 Skills 实现业务知识外置：

```
skills/
├── sql-business-rules/
│   └── SKILL.md          # SQL 业务规则
└── chart-describe/
    ├── SKILL.md          # 图表生成规则
    └── reference/
        ├── bar.md        # 柱图配置模板
        ├── line.md       # 折线图配置模板
        └── pie.md        # 饼图配置模板
```

**工作流程**：
1. Agent 启动时加载 SKILL.md
2. LLM 生成 SQL/图表前先读取 Skills
3. 按 Skills 约束调整输出

**对比我方**：
- 业务规则硬编码在提示词中
- 修改规则需要修改代码或提示词模板

### 4.3 指标库设计

海泰的指标存储在 Milvus 中：

```python
class MetricInfo:
    ie_id: str              # 指标 ID
    ie_code: str            # 指标编码
    ie_name: str            # 指标名称
    ie_unit: str            # 单位
    ie_description: str     # 描述（用于向量检索）
    ie_define: str          # 定义
    ie_complexity: str      # 复杂度：单一/复合
    ie_formula: str         # 计算公式
    factor_ie_ids: List[str] # 子指标 ID 列表
    ie_sql: str             # SQL 模板
```

**复合指标示例**：
```
门诊处方合格率 = 门诊合格处方人次数 / 门诊处方人次数 * 100
```
- 主指标关联子指标 ID
- 查询时自动展开获取子指标 SQL
- 支持跨表聚合计算

### 4.4 CopilotKit 前端集成

海泰使用 CopilotKit 提供 AI 前端组件：

```python
from copilotkit import LangGraphAGUIAgent
from ag_ui_langgraph import add_langgraph_fastapi_endpoint

add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        config={"recursion_limit": 50},
        name="deep-agent",
        description="根据用户自然语言，基于指标生成图表的智能体",
        graph=graph,
    ),
    path="/",
)
```

**特点**：
- **标准 AG-UI 协议**：前后端通过协议通信
- **流式响应**：支持 Agent 思考过程展示
- **开箱即用**：无需自行开发 AI 对话组件

---

## 五、优势与不足对比

### 5.1 我方优势

| 维度 | 说明 |
|------|------|
| **简单易维护** | 线性管道，代码结构清晰，学习曲线低 |
| **轻量部署** | 依赖少（MySQL + Redis），部署简单 |
| **快速响应** | 规则推断图表类型，无需 LLM 参与 |
| **多数据源支持** | MySQL + PostgreSQL，配置灵活 |
| **多租户完善** | 全链路 tenant_id 过滤，RBAC 权限控制 |
| **缓存优化** | 两级缓存（精确 + 语义），减少 LLM 调用 |

### 5.2 海泰优势

| 维度 | 说明 |
|------|------|
| **架构先进** | deepagents + 多 Agent 协作，扩展性强 |
| **状态持久化** | PostgreSQL Checkpointer，支持会话恢复 |
| **向量检索** | Milvus + BGE Embedding，语义检索更精准 |
| **指标库** | 预定义业务指标，支持复合指标计算 |
| **Skills 机制** | 业务规则外置，热更新无需改代码 |
| **追问处理** | 完善的多轮对话上下文补全逻辑 |
| **结构化输出** | Pydantic 强约束，减少 LLM 输出解析问题 |

### 5.3 我方不足

| 维度 | 说明 |
|------|------|
| **无向量检索** | 仅关键词匹配，召回率有限 |
| **无指标库** | 每次查询依赖 LLM 理解 schema |
| **无会话状态持久化** | 多轮对话需手动传递历史 |
| **业务规则硬编码** | 修改规则需改代码 |
| **图表生成简单** | 规则推断，灵活性不足 |

### 5.4 海泰不足

| 维度 | 说明 |
|------|------|
| **部署复杂** | 依赖多（Milvus + PostgreSQL + MongoDB + Oracle） |
| **学习曲线高** | deepagents 框架需要学习 |
| **无多租户设计** | 未看到 tenant_id 隔离逻辑 |
| **单一数据库** | 仅支持 Oracle，无 MySQL/PostgreSQL 支持 |
| **无权限控制** | 未看到 RBAC 或权限校验 |

---

## 六、可借鉴的技术点

### 6.1 高优先级

| 技术 | 说明 | 实施难度 |
|------|------|----------|
| **Skills 机制** | 业务规则外置，通过 SKILL.md 注入 | 中 |
| **结构化意图输出** | Pydantic 约束 LLM 输出格式 | 低 |
| **向量检索指标库** | Milvus + Embedding 实现语义检索 | 高 |
| **ECharts 完整配置生成** | LLM 生成 option 而非规则推断 | 中 |

### 6.2 中优先级

| 技术 | 说明 | 实施难度 |
|------|------|----------|
| **PostgreSQL Checkpointer** | 会话状态持久化 | 中 |
| **PostgresStore** | 结构化状态管理 | 中 |
| **复合指标支持** | 指标关联子指标，公式计算 | 高 |
| **追问补全逻辑** | 相对时间解析 + 省略补全 | 中 |

### 6.3 低优先级

| 技术 | 说明 | 实施难度 |
|------|------|----------|
| **deepagents 框架** | 多 Agent 协作封装 | 高 |
| **CopilotKit 集成** | 前端 AI 组件库 | 中 |
| **Oracle 支持** | 扩展数据库方言 | 中 |

---

## 七、架构升级建议

### 7.1 短期（1-2 周）

1. **结构化意图输出**
   - 将意图识别改为 Pydantic 强约束输出
   - 增加 `normalized_question` 字段，剥离可视化指令
   - 增加 `confidence` 字段用于下游决策

2. **Skills 机制**
   - 创建 `skills/sql-business-rules/SKILL.md`
   - 在 SQL 生成前读取并注入提示词
   - 支持环境变量配置 Skills 路径

3. **ECharts 完整配置**
   - 创建 `skills/chart-describe/` 目录
   - LLM 生成完整 ECharts option
   - 支持图表样式 Skills 约束

### 7.2 中期（1-2 月）

1. **向量检索指标库**
   - 引入 Milvus 或 pgvector
   - 预定义业务指标（名称、描述、SQL、公式）
   - 实现语义检索 + LLM 二次筛选

2. **会话状态持久化**
   - 引入 PostgreSQL Checkpointer
   - 实现会话恢复能力
   - 支持跨轮次状态传递

3. **追问处理优化**
   - 完善相对时间解析逻辑
   - 实现省略补全（继承上一轮维度）
   - 增加历史意图存储

### 7.3 长期（3-6 月）

1. **多 Agent 架构**
   - 评估 deepagents 框架
   - 或自行实现 Leader-Worker 模式
   - 统一状态管理（Store）

2. **复合指标支持**
   - 设计指标关联关系
   - 实现子指标自动展开
   - 支持跨表聚合计算

---

## 八、总结

海泰 ChatBI 在**架构设计**和**技术深度**上明显优于我方：

| 维度 | 海泰优势 |
|------|----------|
| **Agent 框架** | deepagents 封装，支持多 Agent 协作 |
| **状态管理** | PostgreSQL 持久化，支持会话恢复 |
| **向量检索** | Milvus + BGE Embedding，语义检索精准 |
| **业务知识** | Skills 机制，规则外置可热更新 |
| **指标体系** | 预定义指标库 + 复合指标支持 |
| **前端集成** | CopilotKit + AG-UI 协议 |

我方优势在于**简单易维护**和**多租户支持**，适合快速迭代和小团队维护。

建议优先引入 **Skills 机制** 和 **向量检索指标库**，这两个改进对业务价值最大，实施风险可控。

---

*分析完成时间：2026-05-09*
