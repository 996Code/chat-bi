# 海泰 ChatBI 深度技术分析

> 逐行代码解析，挖掘其核心技术优势
> 分析日期：2026-05-10

---

## 一、架构设计精髓

### 1.1 Leader-Worker 多 Agent 协作模式

#### 核心代码：`leader_agent.py`

```python
# 行 93-105：使用 deepagents 创建多 Agent 协作系统
def build_leader_agent(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    debug: bool = True,
):
    return create_deep_agent(
        name="leader_agent",
        context_schema=Context,                                    # 上下文结构定义
        backend=BACKEND,                                           # 文件系统后端
        model=get_model(),
        system_prompt=leader_system_prompt,
        tools=[get_context_by_key, get_intent_history, append_intent_history, now_time],  # Leader 专用工具
        subagents=[intent_classifier, metric_searcher, sql_agent, visualization_agent],    # 子代理列表
        middleware=[],                                             # 中间件（可扩展）
        checkpointer=checkpointer,                                 # 会话检查点
        store=store,                                               # 状态存储
    )
```

**技术要点**：

1. **`create_deep_agent`** 是 `deepagents` 库的核心函数，它封装了 LangGraph 的复杂性
2. **`subagents` 参数**：定义了 4 个子代理，Leader 通过 `task` 工具委派任务
3. **`checkpointer` + `store`**：实现了会话状态持久化，支持中断恢复
4. **`context_schema=Context`**：定义了请求级别的上下文（user_id、user_name）

**对比我方**：
- 我方使用 `StateGraph(QueryState)` 手动构建状态机
- 每个节点是独立函数，通过返回 dict 更新状态
- 无子代理概念，节点间通过状态传递数据

### 1.2 ToolRuntime 与 @tool 装饰器

#### 核心代码：`leader_agent.py` 行 31-54

```python
@tool("get_intent_history", description="获取历史的已解析问题上下文，用于追问补全")
def get_intent_history(runtime: ToolRuntime[Context]) -> list[dict[str, str]]:
    logger.info("开始执行工具get_intent_history",)
    history = get_context(runtime, ContextKey.intent_history)
    if not isinstance(history, list):
        return []
    out: list[dict[str, str]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "normalized_question": str(row.get("normalized_question", "")),
                "user_question": str(row.get("user_question", "")),
            }
        )
    return out
```

**技术要点**：

1. **`@tool` 装饰器**：将函数声明为 LangChain 工具，自动处理参数解析
2. **`ToolRuntime[Context]`**：泛型参数 `Context` 定义了运行时上下文类型
3. **`runtime.store`**：访问 PostgresStore，读取/写入状态
4. **`runtime.config`**：访问配置（如 thread_id）

**对比我方**：
- 我方节点函数直接定义 `async def node(state: QueryState) -> dict`
- 无 ToolRuntime 封装，需手动管理状态

---

## 二、状态管理机制

### 2.1 PostgresStore 与 Namespace

#### 核心代码：`context_tools.py` 行 57-76

```python
def _get_store(runtime:ToolRuntime[Context])->BaseStore:
    return runtime.store

def _get_thread_id(runtime:ToolRuntime[Context])->str:
    configurate = runtime.config.get("configurable")
    if not configurate:
        raise Exception("无法找到thread_id信息!")
    thread_id = configurate.get("thread_id")
    if not thread_id:
        raise Exception("无法找到thread_id信息!")
    return thread_id

def _build_namespace(runtime:ToolRuntime[Context])->Tuple:
    agent_context = _get_context(runtime)
    thread_id = _get_thread_id(runtime)
    return "test_user_id" if not agent_context or not agent_context.user_id else agent_context.user_id,thread_id
```

**技术要点**：

1. **Namespace 设计**：`(user_id, thread_id)` 双层命名空间
   - 第一层：用户隔离（不同用户的数据不混淆）
   - 第二层：会话隔离（同一用户的不同对话不混淆）

2. **状态读写**：
```python
def put_context(runtime:ToolRuntime[Context], key:ContextKey, value:Any):
    store = _get_store(runtime)
    store.put(
        namespace=_build_namespace(runtime),  # (user_id, thread_id)
        key=str(key.value),                   # 如 "metric_context"
        value=_to_jsonable(value),            # 递归序列化
    )

def get_context(runtime:ToolRuntime[Context], key:ContextKey):
    store = _get_store(runtime)
    data:Item = store.get(namespace=_build_namespace(runtime), key=str(key.value))
    return _to_jsonable(data.value) if data else None
```

**对比我方**：
- 我方状态通过 `QueryState` TypedDict 在节点间传递
- 无持久化，每次请求结束后状态丢失
- 多轮对话需手动传递 `history` 参数

### 2.2 ContextKey 枚举与结构化状态

#### 核心代码：`context_tools.py` 行 19-55

```python
class ContextKey(Enum):
    metric_context = "metric_context"           # 指标定义
    tables_context = "tables_context"           # 完整表结构
    zip_table_context = "zip_table_context"     # 精简表结构
    sql_context = "sql_context"                 # 生成的 SQL
    sql_execute = "sql_execute"                 # SQL 执行结果
    visualization_context = "visualization_context"  # 图表配置
    intent_history = "intent_history"           # 历史意图（追问用）

class MetricContext(BaseModel):
    metric_name: str
    metric_unit: str
    metric_type: Literal["single","composite"]  # 单一/复合指标
    metric_sql: str
    calc_expression: str                        # 计算公式
    sub_metrics: List[MetricContext]            # 子指标（递归）
    table_full_names: List[str]

class TableContext(BaseModel):
    schema_name: str
    name: str
    description: str
    column_contexts: List[ColumnContext]

class ColumnContext(BaseModel):
    is_pk: bool
    name: str
    description: str
    data_type: str                              # 关键：类型信息用于 SQL 生成
```

**技术要点**：

1. **ContextKey 枚举**：统一管理所有状态键，避免拼写错误
2. **Pydantic 模型**：强类型约束，自动校验和序列化
3. **递归结构**：`MetricContext.sub_metrics` 支持复合指标嵌套
4. **data_type 字段**：`ColumnContext.data_type` 在 SQL 生成时约束函数使用

**对比我方**：
- 我方状态字段直接定义在 `QueryState` TypedDict 中
- 无枚举管理，字段名容易拼写错误
- 无 `data_type` 字段，SQL 生成时可能用错函数

---

## 三、意图识别系统

### 3.1 结构化输出与 Pydantic 强约束

#### 核心代码：`intent_classifier.py` 行 8-17

```python
class IntentClassifierOutput(BaseModel):
    intent_type: Literal["bi_query", "chart_only", "life"]
    action: Literal["query", "explain", "chart", "other"]
    is_bi: bool
    chart_requested: bool = Field(default=False)
    confidence: float = Field(ge=0.0, le=1.0)       # 置信度 [0, 1]
    user_question: str = Field(default="")          # 用户原话
    normalized_question: str = Field(default="")    # 标准化问题
    reason: str = Field(default="")                 # 分类原因
```

**技术要点**：

1. **`Literal` 类型**：限制字段只能取特定值，LLM 输出必须匹配
2. **`Field(ge=0.0, le=1.0)`**：数值范围约束，confidence 必须在 [0, 1] 之间
3. **双问题设计**：
   - `user_question`：用户原话，用于展示
   - `normalized_question`：标准化后的问题，用于检索
4. **`action` 字段**：区分 query（查询）、explain（解释）、chart（作图）

**对比我方**：
- 我方意图识别只返回 `"DataQuery"` 或 `"Other"` 两个值
- 无标准化问题输出
- 无 action 区分，无法支持 explain 场景

### 3.2 追问补全机制

#### 核心代码：`intent_classifier.py` system_prompt 第 77-91 行

```
13) **相对时间必须展开进 `normalized_question`（不可假装没改时间）**：
   - 若存在 `previous_resolved_context` 且锚点问句里**已出现明确历史时间**（如某年某月），
     本轮用户又使用「上个月/下个月/去年」等相对时间指代，则**必须以该历史时间为基准**
     做日历推算，并把结果写进 `normalized_question`
   - 例如锚点为「…2024年11月…」，本轮「那么上个月的数据呢」
     → 时间维应为 **2024年10月**，不得仍写 2024年11月

14) **续问与历史合并（通用，防过拟合单一场景）**：
   - `normalized_question` = 锚点中**本轮未重写的维度** + 本轮**新增或变更的维度**
   - 本轮明确说到的维度以本轮为准，覆盖锚点同维度
   - 本轮未提到的维度从锚点继承
```

**技术要点**：

1. **相对时间解析**：LLM 负责将"上个月"解析为具体月份（如 2024年10月）
2. **维度继承**：追问时自动继承上一轮的筛选条件（科室、指标等）
3. **冲突处理**：本轮明确说的维度覆盖锚点，未说的继承
4. **Prompt 工程精细度**：意图识别的 system_prompt 长达 92 行，覆盖各种边界情况

**对比我方**：
- 我方的 `context_resolver.py` 只有简单的关键词替换
- 相对时间解析通过正则匹配 `RELATIVE_TIME` 字典
- 无维度继承机制，追问需要用户重复完整条件

---

## 四、指标库与向量检索

### 4.1 Milvus 向量数据库设计

#### 核心代码：`metric_service.py` 行 19-66

```python
class MetricInfo(Model2Schema):
    ie_id: str
    ie_code: str
    ie_name: str
    ie_unit: str
    ie_description: str = Field(default="", description="描述")  # 用于向量检索
    ie_define: str
    ie_complexity: Literal["复合指标", "单一指标"]
    ie_formula: str = Field(default="", description="计算方式")
    factor_ie_ids: List[str]          # 子指标 ID 列表
    ie_sql: str
    score: float = Field(default=0, description="得分", exclude=True)

    def get_schema(self):
        fields = [
            FieldSchema(name="ie_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="ie_description", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="ie_description_vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
            # ... 其他字段
        ]
        collection_schema = CollectionSchema(fields=fields)

        # 内置 Embedding 函数
        collection_schema.add_function(Function(
            name="tei_func",
            function_type=FunctionType.TEXTEMBEDDING,
            input_field_names=["ie_description"],
            output_field_names=["ie_description_vector"],
            params={
                "provider": "openai",
                "model_name": EMBEDDING_MODEL,
                "truncate": "true"
            }
        ))
        return collection_schema
```

**技术要点**：

1. **Milvus Collection 设计**：
   - 标量字段：`ie_id`, `ie_name`, `ie_sql` 等
   - 向量字段：`ie_description_vector`（1024 维，BGE-large-zh）

2. **内置 Embedding 函数**：Milvus 支持自动调用外部 Embedding 服务
3. **复合指标支持**：`factor_ie_ids` 存储子指标 ID，查询时可展开

**对比我方**：
- 我方无指标库概念
- 每次查询时 LLM 需要从完整 schema 中选择表和字段
- 无预定义的业务指标定义

### 4.2 语义检索流程

#### 核心代码：`metric_service.py` 行 90-124

```python
def semantic_search(query, limit=5) -> List[MetricInfo]:
    client = MilvusClient(uri=MILVUS_URL, token="root:Milvus")
    openai_client = OpenAI(base_url=EMBEDDING_URL, api_key=EMBEDDING_API_KEY)
    
    # Step 1: 将查询文本转为向量
    query_vectors = [
        vec.embedding
        for vec in openai_client.embeddings.create(input=query, model=EMBEDDING_MODEL).data
    ]
    
    # Step 2: 向量相似度搜索
    results = client.search(
        collection_name=MetricInfo.get_collection_name(),
        anns_field="ie_description_vector",
        data=query_vectors,
        limit=limit,
        output_fields=['ie_id', 'ie_code', 'ie_name', 'ie_unit', 'ie_description', 
                       'ie_define', 'ie_complexity', 'ie_formula', 'factor_ie_ids', 'ie_sql']
    )
    
    # Step 3: 格式化返回结果
    formatted_results = []
    for hits in results:
        for hit in hits:
            formatted_results.append(MetricInfo(
                ie_id=hit.get("ie_id"),
                ie_name=hit.get("ie_name"),
                score=hit.score  # 相似度分数
                # ...
            ))
    return formatted_results
```

**技术要点**：

1. **两阶段检索**：
   - 第一阶段：向量相似度检索，召回 Top-K 候选
   - 第二阶段：LLM 从候选中选择最匹配的指标

2. **score 字段**：Milvus 返回相似度分数，用于阈值过滤

**对比我方**：
- 我方 Schema 选择完全依赖 LLM 理解 schema 文本
- 无向量检索，召回能力有限

### 4.3 复合指标展开

#### 核心代码：`metric_searcher.py` 行 167-213

```python
def build_metric_context(metric_info: MetricInfo):
    if metric_info.ie_complexity == "单一指标":
        return MetricContext(
            metric_name=metric_info.ie_name,
            metric_sql=metric_info.ie_sql,
            calc_expression=metric_info.ie_formula,
            sub_metrics=[],
            table_full_names=[extract_table_name(metric_info.ie_sql)]
        )

    elif metric_info.ie_complexity == "复合指标":
        table_full_names = []
        sub_metric_contexts = []
        sub_metric_ids = metric_info.factor_ie_ids
        
        # 递归查询子指标
        sub_metric_infos = list_by_ie_ids(sub_metric_ids)
        for sub_metric_info in sub_metric_infos:
            table_full_names.append(extract_table_name(sub_metric_info.ie_sql))
            sub_metric_contexts.append(
                MetricContext(
                    metric_name=sub_metric_info.ie_name,
                    metric_sql=sub_metric_info.ie_sql,
                    calc_expression=sub_metric_info.ie_formula,
                    # ...
                )
            )
        
        return MetricContext(
            metric_name=metric_info.ie_name,
            metric_type="composite",
            calc_expression=metric_info.ie_formula,  # 如 "A / B * 100"
            sub_metrics=sub_metric_contexts,
            table_full_names=list(set(table_full_names))  # 合并所有涉及的表
        )
```

**技术要点**：

1. **递归展开**：复合指标查询时自动展开子指标
2. **表名合并**：`table_full_names` 收集所有子指标涉及的表
3. **公式保留**：`calc_expression` 保存计算公式（如 `A / B * 100`）

**业务示例**：
```
指标：门诊处方合格率
公式：门诊合格处方人次数 / 门诊处方人次数 * 100
子指标：
  - 门诊合格处方人次数（SQL: SELECT ... FROM t1）
  - 门诊处方人次数（SQL: SELECT ... FROM t2）
涉及表：[t1, t2]
```

**对比我方**：
- 我方无指标概念，无法支持复合计算
- 用户需要自己理解表结构和计算逻辑

---

## 五、Skills 机制

### 5.1 业务规则外置

#### 核心代码：`sql_agent.py` 行 246-254

```python
sql_agent = {
    "name": "sql_agent",
    "system_prompt": system_prompt,
    "model": get_model(),
    "tools": [search_schemas_tool, valid_sql_tool, execute_sql_tool],
    "skills": [SQL_BUSINESS_SKILL_PATH],  # 挂载 Skills
    "middleware": [SkillsObserverMiddleware("sql_agent")],  # 中间件
}
```

#### SKILL.md 内容：`skills/sql-business-rules/SKILL.md`

```markdown
---
name: sql-business-rules
description: 生成 SQL 的业务场景规则集合
version: 1.5.1
---

## 时间与 `statistic_time`

1. **只要 SQL 使用了 `statistic_time` 过滤，就不要再使用 `time_dime` 条件**
2. `statistic_time` **必须使用 8 位 `YYYYMMDD` 格式**，禁止 6 位月份值

## 非空规则

1. **生成的 SQL 中，凡出现在查询列里的业务列，都必须在 `WHERE` 中拼接对应的 `IS NOT NULL` 条件**
```

**技术要点**：

1. **Skills 路径配置**：`get_skill_dir("sql-business-rules")` 解析为绝对路径
2. **自动注入**：`deepagents` 框架在 Agent 执行前自动读取 SKILL.md
3. **热更新**：修改 SKILL.md 后无需重启服务，下次请求自动生效

### 5.2 SkillsObserverMiddleware 中间件

#### 核心代码：`skills_observer_middleware.py`

```python
class SkillsObserverMiddleware(AgentMiddleware[AgentState, ContextT, Any]):
    """Observe whether skills are loaded and read in current turn."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    def before_agent(self, state: AgentState, runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        # 检查 Skills 是否加载
        skills_metadata = state.get("skills_metadata", [])
        skill_names = [str(item.get("name", "")) for item in skills_metadata if isinstance(item, dict)]
        logger.info("[%s][skills] loaded_count=%d names=%s", self.agent_name, len(skill_names), skill_names)
        return None

    def after_model(self, state: AgentState, runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        # 检查 LLM 是否读取了 Skills
        last_ai = next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)
        tool_calls = last_ai.tool_calls or []
        read_skill_paths = [
            str(args.get("path", ""))
            for call in tool_calls
            if call.get("name") == "read_file" and "/skills/" in str(args.get("path", ""))
        ]
        logger.info("[%s][skills] used=%s read_skill_paths=%s", 
                    self.agent_name, bool(read_skill_paths), read_skill_paths)
        return None
```

**技术要点**：

1. **`before_agent`**：Agent 执行前检查 Skills 是否加载
2. **`after_model`**：LLM 响应后检查是否调用了 `read_file` 读取 Skills
3. **可观测性**：日志记录 Skills 的加载和使用情况

**对比我方**：
- 我方业务规则硬编码在提示词中
- 修改规则需要修改代码或提示词模板
- 无法热更新

---

## 六、SQL 生成与执行

### 6.1 列筛选机制

#### 核心代码：`sql_agent.py` 行 28-101

```python
@tool("search_schemas_tool", description="获取此次需要生成SQL的前置信息")
def search_schemas_tool(runtime: ToolRuntime[Context], normalized_question: str):
    # 从 Store 读取指标和表结构上下文
    metric_contexts = get_context(runtime, ContextKey.metric_context)
    table_contexts = get_context(runtime, ContextKey.tables_context)
    
    # 对每个表，用 LLM 精简列
    for table_context in table_contexts:
        human_prompt = (
            f"normalized_question:\n{normalized_question}\n\n"
            f"metric_contexts:\n{metric_contexts}\n\n"
            f"column_texts:\n{[column_text.model_dump() for column_text in table_context.column_contexts]}\n"
        )

        class KeepContextTexts(BaseModel):
            keep_context_text_names: List[str] = Field(default_factory=list)
            reason: str = ""

        # 结构化输出
        with_structured = llm.with_structured_output(KeepContextTexts)
        result = with_structured.invoke([
            SystemMessage(content=column_system_prompt), 
            HumanMessage(content=human_prompt)
        ])
        
        # 精简列
        table_context.column_contexts = [
            column_text for column_text in table_context.column_contexts 
            if column_text.name in result.keep_context_text_names
        ]
    
    # 写回精简后的表结构
    put_context(runtime, ContextKey.zip_table_context, [item.model_dump() for item in zip_table_contexts])
```

**技术要点**：

1. **两步列筛选**：
   - 第一步：metric_searcher 获取完整表结构
   - 第二步：sql_agent 根据问题精简列

2. **`with_structured_output`**：强制 LLM 输出 Pydantic 模型格式

**对比我方**：
- 我方在 schema_selection.py 中用 LLM 选择表和列
- 选择后直接构建 schema_context，无二次精简

### 6.2 严格的 SQL 生成约束

#### 核心代码：`sql_agent.py` system_prompt 关键规则

```
【强制流程】
Step 0. 每轮先检查 skill（必须执行）
    - 必须先读取并检查 `sql-business-rules` skill 的当前规则

Step 1. 先调用 search_schemas_tool
    - 入参必须传 normalized_question
    - 若返回 ok=false：立即结束，禁止执行 SQL

Step 2. 基于筛选列生成 SQL
    - 物理列名白名单：只能使用 `table_contexts[].column_contexts[].name` 中出现的列名
    - **必须严格遵守 `data_type`** 选择 SQL 函数
    - 禁止 CTE（`WITH ... AS`）
    - Oracle 别名长度 ≤ 30 字节

Step 2.5. 环境规则辅助对齐
    - 生成首版 SQL 后，按 Step 0 的规则清单自检
    - 若不一致，重写 SQL（最多 1 次）

Step 3. SQL 校验
    - 必须调用 valid_sql_tool 校验

Step 4. 执行
    - execute_sql_tool 最多调用 1 次
    - 成功后立即返回，不得再调用任何工具
```

**技术要点**：

1. **Step 0 强制读取 Skills**：确保业务规则生效
2. **白名单机制**：LLM 只能使用明确列出的列名
3. **单次执行约束**：禁止"失败后改条件重查"
4. **data_type 约束**：字符型列不能直接用日期函数

**对比我方**：
- 我方 SQL 生成后通过 self_heal.py 修复错误
- 无强制 Skills 读取机制
- 无 data_type 约束

---

## 七、会话状态持久化

### 7.1 PostgreSQL Checkpointer

#### 核心代码：`main.py` 行 37-65

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 连接池配置
    async_pool = AsyncConnectionPool(
        POSTGRES_URI,
        min_size=1,
        max_size=10,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    sync_pool = ConnectionPool(POSTGRES_URI, min_size=1, max_size=10, ...)

    # 创建 Checkpointer 和 Store
    checkpointer = AsyncPostgresSaver(async_pool)
    store = PostgresStore(sync_pool, deserializer=_store_deserializer)
    
    # 初始化表结构
    await checkpointer.setup()
    store.setup()

    # 构建 Agent
    graph = build_leader_agent(
        checkpointer=checkpointer,
        store=store,
        debug=True,
    )
```

**技术要点**：

1. **`AsyncPostgresSaver`**：异步保存会话检查点
   - 保存每轮对话的完整状态
   - 支持时间旅行（回到任意历史状态）

2. **`PostgresStore`**：键值存储
   - 存储 `ContextKey` 定义的各阶段数据
   - 支持 namespace 隔离

3. **`thread_id` 隔离**：每个对话有独立的 thread_id
   ```python
   result = agent.stream(
       input={"messages": [{"role": "user", "content": message}]},
       config={"configurable": {"thread_id": thread_id}},
       context=Context(user_id="123", user_name="张三")
   )
   ```

**对比我方**：
- 我方使用 Redis 缓存查询结果
- 无 Checkpointer 概念
- 会话中断后无法恢复

---

## 八、CopilotKit 前端集成

### 8.1 ag-ui-langgraph 协议

#### 核心代码：`main.py` 行 67-76

```python
from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from copilotkit import LangGraphAGUIAgent

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

**技术要点**：

1. **`ag-ui-langgraph`**：Agent UI 协议库
   - 定义了前后端通信的标准格式
   - 支持流式响应、工具调用展示

2. **`LangGraphAGUIAgent`**：将 LangGraph Agent 包装为 AG-UI 兼容格式

**对比我方**：
- 我方使用 SSE（Server-Sent Events）自定义协议
- 前端需要自行解析事件流
- 无标准化 Agent UI 组件

---

## 九、总结：核心技术优势

### 9.1 架构层面

| 技术 | 海泰实现 | 技术价值 |
|------|----------|----------|
| **deepagents 框架** | `create_deep_agent` + subagents | 封装 LangGraph 复杂性，支持多 Agent 协作 |
| **PostgreSQL Checkpointer** | `AsyncPostgresSaver` | 会话状态持久化，支持中断恢复 |
| **PostgresStore** | Namespace + Key-Value | 结构化状态管理，多租户隔离 |
| **ToolRuntime** | `ToolRuntime[Context]` | 类型安全的工具运行时 |

### 9.2 业务层面

| 技术 | 海泰实现 | 技术价值 |
|------|----------|----------|
| **向量指标库** | Milvus + BGE Embedding | 语义检索，召回率更高 |
| **复合指标** | `factor_ie_ids` + 递归展开 | 支持指标组合计算 |
| **Skills 机制** | SKILL.md + Middleware | 业务规则外置，热更新 |
| **结构化意图** | Pydantic + `response_format` | 强约束输出，减少解析错误 |

### 9.3 工程层面

| 技术 | 海泰实现 | 技术价值 |
|------|----------|----------|
| **Pydantic 强约束** | `IntentClassifierOutput` 等 | 类型安全，自动校验 |
| **Middleware** | `SkillsObserverMiddleware` | 可观测性，可扩展 |
| **CopilotKit** | `ag-ui-langgraph` | 标准化 Agent UI |

---

## 十、对我方的借鉴建议

### 10.1 立即可实施

| 改进点 | 工作量 | 收益 |
|--------|--------|------|
| **Skills 机制** | 中 | 业务规则外置，无需改代码 |
| **结构化意图输出** | 低 | 减少解析错误，支持 explain 场景 |
| **normalized_question** | 低 | 剥离可视化指令，提高检索准确率 |

### 10.2 中期规划

| 改进点 | 工作量 | 收益 |
|--------|--------|------|
| **向量指标库** | 高 | 语义检索，召回率提升 |
| **PostgresStore** | 中 | 状态持久化，追问更智能 |
| **复合指标支持** | 高 | 支持复杂业务计算 |

### 10.3 长期规划

| 改进点 | 工作量 | 收益 |
|--------|--------|------|
| **deepagents 框架** | 高 | 架构升级，多 Agent 协作 |
| **CopilotKit 集成** | 中 | 标准化前端，减少开发量 |

---

*分析完成时间：2026-05-10*
