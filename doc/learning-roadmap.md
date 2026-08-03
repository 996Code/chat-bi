# ChatBI v2 学习路线图

> 本文档为想深入了解 ChatBI v2 系统的开发者提供系统化的学习路径。
> 从基础概念到高级架构，按阶段组织，每个阶段都标注了"读什么"和"验证方法"。
>
> **如果你是从 Java/Spring Boot 转过来的开发者**，建议先看第 0 节的"Java 开发者速查"，
> 建立概念映射关系后再进入阶段一。本文档中所有 Python 特有概念都标注了 ⚡ 标记，
> 点击可跳转到第 0 节的对应解释。

---

## 第 0 节：Java 开发者速查（必读）

> 在开始学习之前，先建立 Python ↔ Java 的概念映射。这节不是 Python 教程，而是"已知 Java，怎么理解 Python 对应的东西"。

### 0.1 技术栈对照表

| Python / ChatBI | 类比 Java 技术 | 关键差异 |
|----------------|---------------|---------|
| **FastAPI** | Spring Boot + Spring Web | 路由用装饰器 `@app.get()` 而非注解；原生异步；自动生成 OpenAPI 文档 |
| **Pydantic** | Jackson + Hibernate Validator | 用类型注解同时做校验和序列化，一体两面 |
| **SQLAlchemy async** | JPA / Hibernate + R2DBC | Session ≈ EntityManager，Model ≈ @Entity，但异步 API 更接近 R2DBC |
| **uv** | Maven / Gradle | `uv sync` ≈ `mvn install`，`uv run` ≈ `mvn exec:java`，`uv add` ≈ `mvn dependency:add` |
| **pyproject.toml** | pom.xml / build.gradle | 项目配置 + 依赖声明，但用 TOML 格式而非 XML |
| **.env** | application.yml | 环境变量驱动，`KEY=VALUE` 格式，没有 YAML 层次结构 |
| **pytest** | JUnit 5 | 函数级测试 `def test_xxx():` 而非注解；`assert` 语句而非 `assertEquals()` |
| **APScheduler** | Spring @Scheduled / Quartz | 编程式注册任务，类似 `scheduler.add_job(func, 'interval', minutes=5)` |
| **SQLGlot** | JSqlParser / 通用 SQL 解析器 | 解析 SQL 为 AST，支持多方言转换 |
| **NetworkX** | JGraphT / Guava Graph | Python 图分析库，支持 Dijkstra、社区发现等 |
| **Milvus** | Elasticsearch（向量版） | 存向量而非文本，用 HNSW 索引而非倒排索引 |
| **Redis** | Redis（Java 也有） | 完全一样，缓存/限流/会话管理 |
| **PostgreSQL** | PostgreSQL（Java 也有） | 完全一样，但连接用 asyncpg 驱动 |

### 0.2 Python 语法速查（Java 开发者版）

| ⚡ Python 语法 | Java 类比 | 说明 |
|---------------|----------|------|
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
| `try: ... except E as e:` | `try { } catch (E e) { }` | 异常处理，没有 checked exception |
| `@decorator` | 注解（但更强大） | 装饰器，可以包装函数/类，功能比 Java 注解更强 |
| `__init__.py` | `package com.example;` | 标识目录为 Python 包，Java 不需要 |
| `_name` | `private Name` | 下划线开头表示"内部使用"（约定，非强制） |
| `if __name__ == '__main__':` | `public static void main(String[] args)` | 入口点判断 |

### 0.3 关键概念详解（Java 开发者版）

**⚡ 什么是 Agent？**
```
AI Agent ≠ Spring Agent ≠ JMX Agent

这里的 Agent 是一个"LLM 驱动的工作流引擎"。可以理解为：
- 一个"智能 Service 层"，接收用户问题，输出 SQL 和图表
- 内部有状态机控制执行流程
- 可以自我修正（SQL 错了就重试）
- 可以问用户（不确定时暂停等待输入）

类比 Java：最接近 Spring State Machine + 策略模式的组合
但每个"状态"的执行逻辑是调用 LLM API，而不是 Java 方法
```

**⚡ 什么是 LLM（大语言模型）？**
```
LLM = Large Language Model，如 GPT-4、Claude 等。
可以理解为"一个超级智能的实习生"：
- 你给它一段文字（Prompt），它续写出一段文字（Response）
- 它没有真正的"理解"，但能根据训练数据做出合理的推测
- 它不是 API 接口，不是算法，而是"猜下一个词"的神经网络

系统调用 LLM 的方式：
- 不是 HTTP 请求到某个"AI 服务"
- 是调用 llm_chat() 函数，传入 prompt，返回文本
- 类似于调用一个"超级 String → String 函数"
```

**⚡ 什么是 Embedding / 向量？**
```
Embedding（嵌入）是把文本变成数字向量的过程。
类似于 MD5 哈希，但 MD5 保证"相同输入→相同输出"，
Embedding 保证"语义相似→数字距离近"。

"狗"  → [0.1, 0.3, 0.8, ...]  (1024 个数字)
"猫"  → [0.2, 0.3, 0.7, ...]  (距离近，因为语义相似)
"汽车" → [0.9, 0.1, 0.2, ...]  (距离远，因为语义不相似)

BGE-large-zh-v1.5 是专门做中文嵌入的模型，输出 1024 维向量。
```

**⚡ 什么是向量数据库？**
```
传统数据库：SELECT * FROM users WHERE name = '张三' → 精确匹配
向量数据库：SELECT * ORDER BY distance(query_vec) LIMIT 10 → 相似度搜索

类比：向量数据库 ≈ Elasticsearch 的"更像这份"（More Like This）查询
但专门为向量相似度搜索优化（HNSW 索引）
Milvus 是开源的向量数据库，类似 ES 但用于向量

为什么需要它？
- 用户的自然语言问题需要匹配到数据库表名/列名
- 关键词匹配可能匹配不到（如"销售额"查不到"orders.amount"）
- 向量搜索能找到语义相似的表/列
```

**⚡ 什么是 async/await？**
```python
# Python 的 async/await 类似于 Java 的 CompletableFuture
async def fetch_data():            # 相当于 CompletableFuture<Data> fetchData()
    result = await db.query(sql)   # 相当于 db.query(sql).get()
    return result                  # 相当于 CompletableFuture.completedFuture(result)
```

### 0.4 项目结构映射（Java 开发者视角）

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
├── ai/                           无直接类比，相当于"复杂的 Service 编排层"
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
│
doc/                              文档
├── architecture-v2.md            架构文档（含 Java 速查）
├── code-reference-index.md       代码引用索引
└── learning-roadmap.md           学习路线图（含 Java 速查）
```

### 0.5 快速启动指南（从零开始）

```bash
# 1. 安装 Python 3.12（如已安装可跳过）
#    macOS: brew install python@3.12
#    建议用 pyenv 管理版本（类似 Java 的 SDKMAN!）

# 2. 安装 uv（Python 包管理器，类比 Maven）
#    macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 克隆项目并安装依赖
cd chat-bi
uv sync                    # 相当于 mvn install（安装所有依赖）

# 4. 配置环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env 填写：LLM_URL、LLM_API_KEY、DATABASE_URL 等

# 5. 启动基础设施
docker compose up -d       # 启动 PostgreSQL + Milvus + Redis

# 6. 跑测试确认环境 OK
uv run pytest backend/tests/ -q    # 709 passed

# 7. 启动后端
./start-backend.sh         # 相当于 mvn spring-boot:run

# 8. 启动前端（另一个终端）
cd frontend && npx vite --host 0.0.0.0 --port 5173

# 9. 打开浏览器访问 http://localhost:5173
```

### 0.6 调试技巧

```bash
# 查看日志（相当于 tail -f application.log）
tail -f logs/chatbi.log

# 用 print 调试（相当于 System.out.println）
# 在代码中加入: print(f"xxx = {xxx}")

# 用 pdb 断点调试（相当于手动插入断点）
# 在代码中加入: import pdb; pdb.set_trace()

# 用 IDE 调试：VS Code 或 PyCharm（类似 IntelliJ IDEA）
# 设置断点后，用 --reload 参数启动：
./start-backend.sh --reload

# 跑测试（相当于 mvn test）
uv run pytest backend/tests/ -q
uv run pytest backend/tests/test_intent.py -v  # 单文件测试
```

### 0.7 常见误区

| 误区 | 正确理解 |
|------|---------|
| "Python 没有类型" | 有类型注解（`x: int`），但运行时不做强制检查 |
| "`__init__` 是构造器" | 是，但第一个参数是 `self`（实例引用） |
| "`@dataclass` 像 Lombok" | 类似，但 dataclass 是标准库，不需要插件 |
| "Python 的多线程" | 有 GIL 锁，多线程不能并行 CPU 计算，但异步 I/O 高效 |
| "`async def` 像 `@Async`" | 更底层，是协程而非线程池 |
| "Python 没有接口" | 有 `Protocol`（鸭子类型接口）和 `ABC`（抽象基类） |
| "`__init__.py` 像构造器" | 不是，是包标识文件，内容在导入时执行 |
| "`import` 像 Java 的 import" | Java 编译时静态导入，Python 运行时执行导入的代码 |

---

## 学习路径总览

> **前置知识**: 熟悉 Python 基础语法、了解 SQL 基本查询、了解 REST API 概念。
> **目标**: 能读懂并修改代码即可，不要求全栈精通。
> **时间说明**: 以下时间为"从零到能读代码"的估算，实际取决于个人基础。

```
阶段一：项目背景与产品定位  ──▶  理解"为什么做"和"做什么"
        │
        ▼
阶段二：技术栈基础  ──────────▶  掌握核心技术栈（按需学习，不要求全部精通）
        │
        ▼
阶段三：系统架构  ────────────▶  理解整体架构和模块划分
        │
        ▼
阶段四：核心数据流  ──────────▶  理解一次问答的完整旅程
        │
        ▼
阶段五：深入模块  ────────────▶  逐模块深入理解实现（熟悉为主，不求全部掌握）
        │
        ▼
阶段六：安全与运维  ──────────▶  理解安全体系和运维策略
        │
        ▼
阶段七：演进与扩展  ──────────▶  理解未来方向和扩展点
```

---

## 阶段一：项目背景与产品定位（预计 1-2 天）

### 学习目标
理解 ChatBI 的产品定位、核心价值、以及与竞品的差异。

### 必读文档

| 文档 | 路径 | 内容 | 阅读时长 |
|------|------|------|---------|
| **项目概览** | `doc/v1-archive/项目概览.md` | 产品核心价值、技术栈、架构决策 | 30 分钟 |
| **Proposal** | `doc/chatbi-v2/proposal.md` | 为什么要做 v2、5 条设计法则、P0/P1/P2 目标 | 10 分钟 |
| **Design** | `doc/chatbi-v2/design.md` | 技术栈、Leader-Worker 架构、Agent 执行流程 | 15 分钟 |
| **需求文档 v2** | `doc/需求文档-v2.md` | v2 需求清单 | 20 分钟 |
| **INDEX** | `doc/chatbi-v2/INDEX.md` | 所有文档的索引和阅读顺序 | 2 分钟 |
| **CLAUDE.md** | `CLAUDE.md` | 项目说明、设计法则、开发规范 | 5 分钟 |

### 核心概念

- **NL2SQL (Natural Language to SQL)**: 自然语言转 SQL，产品的核心能力
- **语义层 (Semantic Layer)**: 数据库表结构的中文描述层，解决 Schema Linking 准确率问题
- **Agent 循环**: 不是一次 LLM 调用，而是"生成→校验→执行→自愈→自检→修正→再生成"的 while(true) 循环
- **Fail-Closed**: 安全默认是"否"，出问题时显式失败，绝不静默放行

### 验证方法

读完这阶段后，你应该能回答：
1. ChatBI 解决什么核心问题？目标用户是谁？
2. v2 的 5 条设计法则是什么？
3. 为什么需要语义层而不是直接用 LLM 理解数据库？
4. 竞品分析中，WrenAI 做得对的地方和踩过的坑分别是什么？

### 动手练习

1. 阅读 `doc/chatbi-v2/proposal.md`，对比 v1 和 v2 的核心差异
2. 阅读 `doc/经验教训.md`，找出 3 条你认为最重要的教训并记下来

---

## 阶段二：技术栈基础（预计 7-14 天，按需学习）

> ⚠️ 不要求精通全部技术栈，目标是"能读懂代码"。建议优先学习加粗标注的核心技术，其余遇到时再查文档。

### 学习目标
掌握 ChatBI 使用的核心技术栈，能够阅读和理解代码。

### 后端技术栈

| 技术 | 用途 | 学习资源 | 预估时间 |
|------|------|---------|---------|
| **Python 3.12** | 开发语言 | 官方教程 | 1 天（熟悉即可） |
| **FastAPI** | Web 框架 | [FastAPI 官方教程](https://fastapi.tiangolo.com/tutorial/) | 2 天 |
| **SQLAlchemy async** | ORM | [SQLAlchemy 2.0 教程](https://docs.sqlalchemy.org/en/20/orm/quickstart.html) | 2 天 |
| **Pydantic v2** | 数据校验 | [Pydantic 官方文档](https://docs.pydantic.dev/latest/) | 1 天 |
| **LangGraph** | 状态机编排 | [LangGraph 教程](https://langchain-ai.github.io/langgraph/tutorials/) | 2 天 |
| **SQLGlot** | SQL 解析 | [SQLGlot GitHub](https://github.com/tobymao/sqlglot) | 1 天（了解即可） |
| **APScheduler** | 定时任务 | [APScheduler 文档](https://apscheduler.readthedocs.io/) | 半天 |

### 数据存储技术

| 技术 | 用途 | 学习资源 | 预估时间 |
|------|------|---------|---------|
| **PostgreSQL** | 元数据库 | [PG 官方教程](https://www.postgresql.org/docs/current/tutorial.html) | 1 天 |
| **Milvus** | 向量数据库 | [Milvus 入门](https://milvus.io/docs/overview.md) | 1 天 |
| **Redis** | 缓存/限流 | [Redis 教程](https://redis.io/docs/getting-started/) | 1 天 |
| **BGE-large-zh** | 中文嵌入模型 | [BGE 模型介绍](https://github.com/FlagOpen/FlagEmbedding) | 半天 |

### 前端技术栈

| 技术 | 用途 | 学习资源 | 预估时间 |
|------|------|---------|---------|
| **Vue 3 + TypeScript** | 前端框架 | [Vue 3 官方教程](https://vuejs.org/guide/introduction.html) | 3 天 |
| **Element Plus** | UI 组件库 | [Element Plus 文档](https://element-plus.org/zh-CN/) | 1 天 |
| **ECharts** | 图表库 | [ECharts 教程](https://echarts.apache.org/handbook/zh/get-started/) | 1 天 |
| **G6 v5** | 图可视化 | [G6 文档](https://g6.antv.antgroup.com/) | 1 天 |
| **GridStack** | 拖拽布局 | [GridStack 文档](https://github.com/gridstack/gridstack.js) | 半天 |
| **ExcelJS** | Excel 导出 | [ExcelJS GitHub](https://github.com/exceljs/exceljs) | 半天 |

### 关键参考文档

| 文档 | 路径 | 内容 |
|------|------|------|
| **Claude Code 源码深度解读** | `doc/Claude-Code-源码深度解读.md` | 3043 行完整分析，设计法则来源 |
| **海泰 ChatBI 完整代码分析** | `doc/海泰ChatBI完整代码分析.md` | 24 个文件的逐行解析，BI 领域打法 |
| **经验教训** | `doc/经验教训.md` | v1 的 48 条坑，别重蹈覆辙 |

---

## 阶段三：系统架构（预计 1-2 天）

### 学习目标
理解 ChatBI v2 的分层架构、模块划分和模块间依赖关系。

### 必读文档

| 文档 | 路径 | 内容 |
|------|------|------|
| **架构图** | `doc/architecture-v2.md` | 本文档第 1 章（整体架构总览） |
| **Design** | `doc/chatbi-v2/design.md` | 核心架构设计 |

### 体系结构速览

```
┌─────────────────────────────────────────────────────────────────────┐
│                   前端 (Vue 3 + TypeScript)                          │
│  9 个功能页面: Chat / DataSource / Semantic / Dashboard / History   │
│  / Observability / Skills / Memory / Login                         │
│  API 层: Axios + JWT 拦截器 + SSE 流式接收                         │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP / SSE
┌────────────────────────────▼────────────────────────────────────────┐
│                   后端 (FastAPI / Python 3.12)                       │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  API 路由层 (api/) — 12 个子路由                                │ │
│  │  认证 / 同步问答 / 流式问答 / 数据源 / 语义层 / 看板 / 图谱 /    │ │
│  │  可观测性 / Skills / 记忆 / 保存查询 / 开发模式                  │ │
│  └────────────────────────┬───────────────────────────────────────┘ │
│                           │                                         │
│  ┌────────────────────────▼───────────────────────────────────────┐ │
│  │  Agent 执行引擎 (ai/) — 14 个模块                               │ │
│  │  intent → schema_search → thinking → generate_sql → execute    │ │
│  │  → heal → check → visualize → final                            │ │
│  │  state_store / compressor / recall / schema_utils / chat_utils  │ │
│  └────────────────────────┬───────────────────────────────────────┘ │
│                           │                                         │
│  ┌────────────────────────▼───────────────────────────────────────┐ │
│  │  业务服务层 (services/) — 15 个服务                             │ │
│  │  embedder / retriever / vector_store / sql_executor /           │ │
│  │  datasource_engine / graph_service / semantic_scanner /         │ │
│  │  knowledge_graph / indexer / fewshot / skills_loader 等         │ │
│  └────────────────────────┬───────────────────────────────────────┘ │
│                           │                                         │
│  ┌────────────────────────▼───────────────────────────────────────┐ │
│  │  基础设施层 (core/) — 18 个核心模块                             │ │
│  │  config / auth / security / llm_client / sql_validator /        │ │
│  │  redis_client / milvus_client / scheduler / token_tracker /    │ │
│  │  prompt_capture / startup_probe / checkpointer 等               │ │
│  └────────────────────────┬───────────────────────────────────────┘ │
│                           │                                         │
│  ┌────────────────────────▼───────────────────────────────────────┐ │
│  │  数据持久化层 (db/) — session + 10 张 ORM 模型                  │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 验证方法

1. 画一张系统架构图（不用看文档，自己默画）
2. 说出每个层的职责和关键模块
3. 说出 API 层 → Agent 层 → 服务层 → 基础设施层的调用链

### 动手练习

1. 运行 `uv run pytest backend/tests/ -q`，确认 700+ 测试全部通过
2. 查看 `backend/app/core/config.py`，找出所有 CHANGE_ME 占位符配置项
3. 阅读 `backend/app/main.py`，画出 lifespan 的启动流程图

---

## 阶段四：核心数据流（预计 1-2 天）

### 学习目标
深刻理解"一次问答"的完整旅程，从用户输入到最终响应的全过程。

### 必读文档

| 文档 | 路径 | 内容 |
|------|------|------|
| **架构图** | `doc/architecture-v2.md` 第 2 章（核心数据流） |
| **Agent 执行引擎 Spec** | `doc/chatbi-v2/specs/agent-execution-engine/spec.md` | 详细 Agent 设计 |
| **agent.py** | `backend/app/ai/agent.py` | 源码级理解 |

### 一问一答的完整旅程

```
用户： "各品类本月销售额，用柱状图展示"
  │
  ├── 1. API 入口 (chat.py / chat_stream.py)
  │     ├── JWT 鉴权 → 获取 tenant_id / user_id
  │     ├── 确定数据源 → 取语义层 content + 连接 URL
  │     ├── 装配 AgentDeps (注入所有依赖)
  │     ├── 恢复多轮上下文 (StateStore 读上一轮)
  │     └── 启动 Token 追踪 + Prompt 捕获
  │
  ├── 2. 意图识别 (intent.py)
  │     ├── LLM 分类: TEXT_TO_SQL / CLARIFICATION / GENERAL / CHART_MODIFY / EXPLANATION
  │     ├── 剥离可视化措辞: "各品类本月销售额" (chart_type_hint=bar)
  │     └── confidence < 0.6 → 降级为 CLARIFICATION
  │
  ├── 3. Schema 检索 (retriever.py)
  │     ├── 阶段1: BGE 嵌入 → Milvus 召回 top-K (K=20)
  │     ├── 阶段2: LLM 精筛 (宁缺毋滥)
  │     ├── 图谱扩展: 沿关系图补全关联表
  │     └── JOIN 路径预计算: Dijkstra 最短路径
  │
  ├── 4. 预思考 (thinking.py)
  │     ├── 选表理由: "为什么选 category 和 orders 表"
  │     ├── 聚合方式: "按 category 分组, SUM(amount)"
  │     └── 注意事项: "注意订单金额含退款"
  │
  ├── 5. SQL 生成 (sql_agent.py)
  │     ├── Prompt 分层: 语义层(可缓存) + Skills + 记忆 + 预思考 + 历史 + 问题
  │     ├── Few-shot 注入: 相似审核 SQL 示例
  │     ├── JOIN 路径注入: 预计算的最短路径
  │     └── 指标定义注入: 业务指标公式
  │
  ├── 6. SQL 校验 (sql_validator.py)
  │     ├── Layer 1: AST 解析 → 拒绝非 SELECT
  │     ├── Layer 2: 危险函数 → 拒绝 LOAD_FILE/SLEEP 等
  │     └── Layer 3: 白名单列 → 只允许语义层定义的列
  │
  ├── 7. SQL 执行 (sql_executor.py)
  │     ├── 连接池复用 (DataSourceEnginePool)
  │     ├── READ ONLY 事务 + DB 侧超时 + Python 侧超时
  │     ├── 自动 LIMIT (防全表扫描 OOM)
  │     └── 结果采样 + truncated 标记
  │
  ├── [自愈循环] 如果失败 → sql_healer.py (最多 2 轮)
  │     ├── 错误码提取 → 分类 → 纠正 prompt → 重新生成
  │     └── 熔断器: 连续 3 次跨查询失败 → 熔断
  │
  ├── 8. 结果自检 (result_checker.py)
  │     ├── 零行检查、全 NULL 检查、笛卡尔积检测
  │     └── 异常 → 自动修正 (带配额) → 仍异常 → ask_user
  │
  ├── 9. 图表生成 (chart_agent.py)
  │     ├── LLM → ECharts Option → JSON 自愈 → 规则兜底
  │     └── KPI 指标卡 / 折线图 / 柱状图 / 饼图 / 表格
  │
  └── 10. 持久化与审计
        ├── StateStore 持久化 (JSONL)
        ├── SavedQuery 入库 (去重)
        ├── Few-shot 回流 (向量库索引)
        ├── 指标反哺 (co_occurrence 增量更新)
        └── 审计日志 (success/fail/denied 三态)
```

### 动手练习

1. 不查文档，完整描述一次问答的 10 个步骤
2. 说出每个步骤的输入和输出
3. 说出每个步骤的失败处理策略
4. 画出自愈循环的流程图
5. 设置断点在 `run_agent()` 的 `intent` 阶段，跟踪一次完整的 TEXT_TO_SQL 问答

### BI 领域补充知识

在深入代码之前，先了解几个 BI 领域的关键概念，这对理解系统的设计决策至关重要：

**Fan Trap（扇形陷阱）**:
```
当从一个表出发 JOIN 到两个 "一对多" 表时，聚合结果会翻倍。
示例: 订单(1) → (N) 订单项, 订单(1) → (N) 付款记录
SELECT SUM(amount) 会重复计算订单金额
```
- 系统的应对: 语义层显式定义关系基数, LLM 生成 JOIN 时引用关系 ID

**Chasm Trap（裂谷陷阱）**:
```
当两个 "多对一" 关系从同一个表出发时，JOIN 后数据丢失。
示例: 订单项 → (N) 产品, 订单项 → (N) 供应商
```
- 系统的应对: 预计算的 JOIN 路径 (Dijkstra) 优先走确定性路径

**Schema Linking（Schema 关联）**:
- 行业数据表明 29%-49% 的 Text-to-SQL 错误来自 Schema Linking 失败
- 系统的语义层 + 两阶段检索 + 图谱扩展 三重机制解决此问题
- 详见 V1 规划文档 `doc/v1-archive/规划文档.md` 第 288-350 行

**评估体系**:
- 系统目前没有自动评估流水线, 依赖人工审核 + 测试集
- 未来方向: 构建内部测试集, 跟踪准确率指标
- 详见 `doc/chatbi-v2/EVOLUTION-ROADMAP.md` 方向 5

---

## 阶段五：深入模块（预计 10-20 天，熟悉为主）

> ⚠️ 本阶段按"熟悉为主，不求全部掌握"的原则设计。建议先看第 5.1 节 Agent 执行引擎（核心中的核心），
> 再按 5.3 → 5.2 → 5.4 → 5.5 的顺序深入。每节推荐阅读 code-reference-index.md 对应章节快速定位代码。
> 学习建议：边读代码边跑测试，遇到问题用 `grep` 或 `code-reference-index.md` 追踪函数定义。

### 学习目标
逐模块深入理解实现细节，能够独立修改和扩展。

### 5.1 Agent 执行引擎 (ai/)

| 模块 | 文件 | 核心内容 | 学习重点 |
|------|------|---------|---------|
| 状态机编排 | `agent.py` | `AgentStage` 枚举, `AgentState` 状态, `run_agent()` 主循环 | while(true) 循环实现, 自愈回路 |
| 意图识别 | `intent.py` | 5 意图分类, Pydantic 强约束, confidence 降级, 可视化措辞剥离 | 如何用 LLM 做分类 + 降级策略 |
| 预思考 | `thinking.py` | LLM 分析选表理由/聚合方式/陷阱 | 思考结果如何注入 SQL 生成 |
| SQL 生成 | `sql_agent.py` | Prompt 分层, Few-shot 注入, Skills 注入, 指标定义 | 提示工程架构 |
| 自愈 | `sql_healer.py` | 错误码提取, 分类纠正, 熔断器 | 错误恢复模式 |
| 图表生成 | `chart_agent.py` | LLM → ECharts JSON, JSON 自愈, 规则兜底 | 多策略图表生成 |
| 结果自检 | `result_checker.py` | 纯规则检查: 0行/全NULL/笛卡尔积 | 规则引擎模式 |
| 状态管理 | `state_store.py` | JSONL 持久化, 追问恢复, 维度继承 | 多轮对话状态恢复 |
| 上下文压缩 | `compressor.py` | Token 阈值触发, LLM 摘要, 状态补偿, 熔断器 | 压缩+补偿模式 |
| 记忆召回 | `recall.py` | 关键词相关召回, 指标反哺, 链路记忆 | 不调 LLM 的召回策略 |
| 自然语言回复 | `replier.py` | GENERAL/EXPLANATION 意图回复, 兜底文案 | 降级策略 |
| 示例问题生成 | `question_generator.py` | LLM 生成 + 规则兜底 | 冷启动引导 |
| 共享工具 | `chat_utils.py` | 值归一化, 追问表继承, 预思考序列化 | 多模块共享逻辑 |
| 用户澄清 | `ask_user.py` | Schema 不确定/结果不明确时暂停 | 人机交互决策点 |
| Schema 工具 | `schema_utils.py` | 白名单列提取, 关系扩展, JOIN 路径 | 图谱驱动的 Schema 构建 |

### 5.2 业务服务层 (services/)

| 模块 | 文件 | 核心内容 | 学习重点 |
|------|------|---------|---------|
| 向量嵌入 | `embedder.py` | 本地 BGE, Protocol 抽象, 惰性加载, LRU 缓存 | 抽象接口设计 |
| 两阶段检索 | `retriever.py` | 向量召回 + LLM 精筛, 宁缺毋滥 | 检索准确率优化 |
| 向量存储 | `vector_store.py` / `milvus_vector_store.py` | 抽象接口, Milvus/Mock 实现 | 接口抽象 + 多实现 |
| SQL 执行 | `sql_executor.py` | 连接池复用, 超时双保险, 自动 LIMIT | 安全执行策略 |
| 数据源连接池 | `datasource_engine.py` | 动态连接池, Fernet 解密, 懒加载 | 动态资源管理 |
| 图谱服务 | `graph_service.py` | NetworkX 图, Dijkstra 路径, 社区发现, 中心度 | 图算法应用 |
| 语义扫描 | `semantic_scanner.py` | SQLAlchemy Inspector → 语义层 JSON, LLM 中文推断 | 元数据自动提取 |
| 图谱推断 | `knowledge_graph.py` | name_pattern + LLM 推断关系, source+confidence 标注 | 知识图谱演化 |
| 索引构建 | `indexer.py` / `indexer_update.py` | 语义层 → 向量索引, 增量更新 | 索引生命周期 |
| Few-shot | `fewshot.py` | 审核 SQL 示例检索, 向量索引 | 示例学习策略 |
| 元数据刷新 | `metadata_refresher.py` | 自动检测 schema 变更, 增量合并 | 后台运维 |
| 数据源健康检查 | `datasource_health.py` | 定时 ping, 失败计数, 自动恢复 | 运维可靠性 |
| 语义层差异比较 | `semantic_diff.py` | 表级 + 列级 diff, 纯函数 | 版本管理 |
| Skills 加载 | `skills_loader.py` | SKILL.md 解析, 热更新, 多租户隔离 | 插件式设计 |

### 5.3 基础设施层 (core/)

| 模块 | 文件 | 核心内容 | 学习重点 |
|------|------|---------|---------|
| 配置管理 | `config.py` | Pydantic Settings, 环境变量, 数值校验 | 配置集中化模式 |
| 认证鉴权 | `auth.py` / `security.py` | JWT, RBAC, 多租户, 审计, 密码加密 | 安全体系 |
| 文件记忆 | `agent_memory.py` | .md 文件存储, YAML frontmatter, 整理 | 文件持久化 |
| Prompt 缓存 | `prompt_cache.py` | TTL 缓存, 分层边界, 段组装 | 性能优化 |
| JSON 解析 | `llm_json.py` | LLM JSON 提取, 括号匹配, 容错 | 健壮性 |
| LLM 客户端 | `llm_client.py` | AsyncOpenAI 封装, 重试/退避, Token 追踪 | 可靠 LLM 调用 |
| SQL 校验 | `sql_validator.py` | 三层校验: AST + 危险函数 + 白名单列 | 安全防御 |
| 启动探测 | `startup_probe.py` | 必需服务 fail-fast, 可选服务降级 | 启动可靠性 |
| 定时调度 | `scheduler.py` | 健康检查, 元数据刷新, 任务清理 | 后台任务管理 |
| 日志配置 | `logging.py` | 控制台 + 每日轮转, 14 天保留 | 可观测性 |
| Token 追踪 | `token_tracker.py` | 请求级 contextvar, 每节点追踪 | 可观测性 |
| Prompt 捕获 | `prompt_capture.py` | 请求级 prompt 记录, dump-prompts 导出 | 调试能力 |

### 5.4 前端模块 (frontend/)

| 页面 | 文件 | 核心内容 | 学习重点 |
|------|------|---------|---------|
| 对话问答 | `ChatView.vue` | SSE 流式接收, 管线可视化, ECharts 渲染, 历史管理 | 流式 UI 模式 |
| 数据源管理 | `DataSourceView.vue` | CRUD, 扫描进度轮询, 健康检查 | 状态管理 + 轮询 |
| 语义层编辑 | `SemanticView.vue` | 表/列/关系/指标编辑, 版本管理, 图谱可视化 | 复杂表格编辑 |
| 看板管理 | `DashboardView.vue` | GridStack 拖拽, 实时查询, 图表渲染 | 拖拽布局 |
| 登录注册 | `LoginView.vue` | 登录/注册/开发模式, JWT 存储 | 认证流程 |
| 历史记录 | `HistoryView.vue` | 审计日志, 慢查询, 对话历史 | 数据展示 |
| 系统监控 | `ObservabilityView.vue` | 健康状态, Prompt 追踪, 数据源指标 | 可观测性 |
| 记忆管理 | `MemoryView.vue` | 记忆 CRUD, 整理, 图谱同步冲突 | 文件管理 |
| 业务规则 | `SkillsView.vue` | Skills CRUD, 预览, 热更新 | 插件式管理 |
| API 客户端 | `client.ts` | Axios + JWT 拦截器 + 自动刷新 | HTTP 拦截器模式 |
| 图谱组件 | `SchemaGraph.vue` | G6 v5 集成, 节点/边操作, 搜索过滤 | 图可视化集成 |
| 对话详情 | `ConversationDetailDrawer.vue` | 轮次详情, 预思考, SQL, Token 用量 | 复用抽屉组件 |

### 5.5 数据模型 (db/)

| 表 | 模型 | 用途 | 核心字段 |
|----|------|------|---------|
| tenants | 租户 | 多租户 | id, name, is_active |
| users | 用户 | 认证 | email, hashed_password, role (admin/user/read_only) |
| data_sources | 数据源 | 业务库连接 | db_type, encrypted_password, scan_status |
| semantic_models | 语义模型 | 版本化语义层 | content (JSON), version, is_current |
| conversations | 对话 | 对话历史 | title, state_json, is_archived |
| saved_queries | 保存查询 | 成功的 SQL 查询 | question, sql_text, chart_config |
| audit_logs | 审计日志 | 审计追踪 | resource_type, action, status (s/f/d), is_slow |
| dashboards | 看板 | 看板容器 | name |
| dashboard_widgets | 看板组件 | 图表组件 | question, query_sql, chart_type, position |

### 验证方法

对每个模块，尝试回答：
1. 这个模块解决什么问题？
2. 输入是什么？输出是什么？
3. 失败时怎么处理？
4. 有哪些设计决策值得学习？

---

## 阶段六：安全与运维（预计 1-2 天）

### 学习目标
理解系统的安全体系、多租户隔离、审计日志和运维策略。

### 必读文档

| 文档 | 路径 | 内容 |
|------|------|------|
| **安全框架 Spec** | `doc/chatbi-v2/specs/security-framework/spec.md` | 安全设计 |
| **架构图 第 9 章** | `doc/architecture-v2.md` | 安全架构总览 |
| **操作手册** | `doc/操作手册.md` | 部署、配置、故障排查 |

### 安全层次

| 层次 | 措施 | 代码位置 |
|------|------|---------|
| 启动安全 | CHANGE_ME 占位符检测, 必需服务探测 | `core/config.py`, `core/startup_probe.py` |
| 传输安全 | CORS 配置, JWT 鉴权, 限流 | `core/auth.py`, `core/rate_limit.py` |
| 认证授权 | JWT 四字段, RBAC 三角色, 多租户 TenantMixin | `core/auth.py`, `db/models.py` |
| SQL 执行安全 | 三层校验, READ ONLY 事务, 自动 LIMIT, 超时 | `core/sql_validator.py`, `services/sql_executor.py` |
| 数据安全 | Fernet 加密, Unicode 清洗, 审计日志 | `core/security.py`, `core/text_sanitize.py` |
| Prompt 安全 | 宁缺毋滥, 自愈后校验, 白名单列 | `ai/retriever.py`, `ai/agent.py` |

### 多租户实现

```
TenantMixin (db/models.py):
  - tenant_id 字段自动注入
  - tenant_filter() 方法显式过滤
  - 所有 tenant-scoped 模型继承 TenantMixin

contextvars (core/auth.py):
  - set_current_tenant() / get_current_tenant()
  - 请求级上下文, 不污染全局

Skills 隔离:
  - skills/{tenant_id}/ 目录
  - SkillsLoader(base_dir=f"skills/{tenant_id}")

Memory 隔离:
  - memory/{tenant_id}/{data_source_id}/
  - AgentMemoryStore 按目录隔离

数据隔离:
  - 所有 SQL 查询加 tenant_id 过滤
  - 不靠手动 WHERE (v1 教训 #48)
```

### 验证方法

1. 说出 6 层安全防御的每一层
2. 解释多租户隔离的实现方式
3. 说出审计日志的三种状态
4. 解释启动时发现 CHANGE_ME 占位符会怎样

---

## 阶段七：演进与扩展（预计 1 天）

### 学习目标
理解系统的未来方向、待扩展的能力和如何参与开发。

### 必读文档

| 文档 | 路径 | 内容 |
|------|------|------|
| **演进路线图** | `doc/chatbi-v2/EVOLUTION-ROADMAP.md` | 未来 13 个方向 |
| **ROADMAP** | `.planning/ROADMAP.md` | 当前阶段规划 |
| **STATE** | `.planning/STATE.md` | 当前状态和遗留债 |
| **任务清单** | `openspec/changes/chatbi-v2/tasks.md` | 任务详情 |

### 未来方向（按优先级）

| 方向 | 状态 | 描述 | 难度 |
|------|------|------|------|
| 查询反哺知识图谱 | ✅ 已完成 | 链路经验沉淀 + 图谱反哺 | 中 |
| 复合指标 | ❌ 未开始 | 指标可引用子指标 | 中 |
| 对话内多轮精准追问 | ❌ 未开始 | 上下文感知的追问 | 中 |
| 检索质量提升 | ❌ 未开始 | 多路召回 + 重排序 | 中 |
| 看板快照模式 | ❌ 未开始 | 定时刷新 + 推送 | 低 |
| 数据源方言扩展 | ❌ 未开始 | Oracle, ClickHouse | 高 |
| 多模态输出 | ❌ 待研究 | 时序预测, 异常检测 | 高 |
| 缓存预热 | ❌ 未开始 | 常用查询预缓存 | 低 |
| 前端性能优化 | ❌ 未开始 | 虚拟列表, 懒加载 | 低 |
| 系统监控增强 | ❌ 未开始 | 指标看板, 告警 | 低 |
| 多表 JOIN 精度 | ❌ 待研究 | 5+ 表复杂 JOIN | 高 |
| 前端用户体验 | ❌ 未开始 | 拖拽引用, Markdown | 低 |
| 降低 LLM 调用频次 | ❌ 待研究 | 缓存, 预计算 | 高 |

### 开发工作流

```
/ai:spec  →  /ai:plan  →  /ai:do  →  /ai:check
 需求定义     规划        执行       全面审查

命令定义在 .claude/commands/ai/
规格产出在 openspec/changes/chatbi-v2/ (OpenSpec 方法论)
执行计划在 .planning/phases/chatbi-v2/ (GSD 结构)
```

### 贡献指南

1. 阅读 `CONTRIBUTING.md` 了解贡献流程
2. 阅读 `SECURITY.md` 了解安全策略
3. 遵循 5 条设计法则
4. 跑测试再交付: `uv run pytest backend/tests/ -q`
5. 配置不硬编码: 所有魔法数字进 `config.py`
6. Python 3.12+ 语法: 用 `X | None` 不用 `Optional[X]`

---

## 附录

### A. 常用命令

```bash
# 跑测试
uv run pytest backend/tests/ -q

# 启动后端
./start-backend.sh

# 启动前端
cd frontend && npx vite --host 0.0.0.0 --port 5173

# 查看任务进度
openspec list

# 查看当前状态
cat .planning/STATE.md

# 查看任务清单
cat openspec/changes/chatbi-v2/tasks.md
```

### B. 参考文档索引

| 文档 | 路径 | 优先级 | 关键内容 |
|------|------|--------|---------|
| 项目状态 | `.planning/STATE.md` | P0 | 当前状态、已完成、遗留债、下一步 |
| 架构图 | `doc/architecture-v2.md` | P0 | 完整架构总览 + 代码引用索引 |
| 代码引用索引 | `doc/code-reference-index.md` | P0 | 460+ 关键定义 file:line 索引 |
| Agent 执行引擎 Spec | `doc/chatbi-v2/specs/agent-execution-engine/spec.md` | P1 | 详细 Agent 设计 |
| 语义层 Spec | `doc/chatbi-v2/specs/semantic-layer/spec.md` | P1 | 语义层设计 |
| RAG 检索 Spec | `doc/chatbi-v2/specs/rag-retrieval/spec.md` | P1 | 两阶段检索 |
| 安全框架 Spec | `doc/chatbi-v2/specs/security-framework/spec.md` | P1 | 安全设计 |
| Claude Code 源码解读 | `doc/Claude-Code-源码深度解读.md` | P1 | 设计法则来源 |
| 海泰 ChatBI 分析 | `doc/海泰ChatBI完整代码分析.md` | P1 | BI 领域打法 |
| 经验教训 | `doc/经验教训.md` | P1 | v1 48 条坑 |
| 上下文压缩 Spec | `doc/chatbi-v2/specs/context-compression/spec.md` | P2 | 压缩设计 |
| 意图识别 Spec | `doc/chatbi-v2/specs/intent-classification/spec.md` | P2 | 意图分类 |
| Skills Spec | `doc/chatbi-v2/specs/skills-business-rules/spec.md` | P2 | 业务规则 |
| 实操手册 | `doc/操作手册.md` | P2 | 部署、配置、API |
| 演进路线图 | `doc/chatbi-v2/EVOLUTION-ROADMAP.md` | P2 | 未来方向 |

### C. 学习路径时间表

| 阶段 | 内容 | 预估时间 | 产出 |
|------|------|---------|------|
| 一 | 项目背景与产品定位 | 1-2 天 | 产品理解 |
| 二 | 技术栈基础 | 7-14 天 | 技术能力（按需学习） |
| 三 | 系统架构 | 1-2 天 | 架构理解 |
| 四 | 核心数据流 | 1-2 天 | 流程理解 |
| 五 | 深入模块 | 10-20 天 | 模块理解（熟悉为主） |
| 六 | 安全与运维 | 1-2 天 | 安全理解 |
| 七 | 演进与扩展 | 1 天 | 方向理解 |
| **总计** | | **22-43 天** | **全栈掌握（熟悉为主）** |

### D. 速查表：项目中的经典设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| **单例 + 惰性加载** | 所有 `get_*()` 函数 | 构造不加载, 首次使用时才初始化 |
| **Protocol 抽象** | `Embedder`, `VectorStore` | 接口定义, 可切换实现 |
| **依赖注入** | `AgentDeps` dataclass | 测试可 mock, 生产接真实服务 |
| **状态机** | `AgentStage` + `run_agent()` | 严格前向, 上游失败直接 final |
| **熔断器** | `SelfHealCircuitBreaker`, `CompressionCircuitBreaker` | 连续失败后停止, 避免浪费 |
| **Fail-Closed** | 所有安全相关 | 出问题拒绝而非放行 |
| **宁缺毋滥** | 检索/匹配/识别 | 失败返回空, 禁止 fallback |
| **while(true) 循环** | SQL 生成→执行→自愈→再执行 | 不是一次调用, 是循环 |
| **压缩+补偿** | 压缩后补回语义层 + SQL + 筛选 | 状态补偿防止信息丢失 |
| **分层缓存** | Prompt 中可缓存/不可缓存分离 | 语义层/Skills 可缓存 |

---

*本文档基于 ChatBI v2 源码分析和文档体系生成，更新于 2026-08-03*