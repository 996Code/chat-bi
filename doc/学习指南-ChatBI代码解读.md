# ChatBI 学习指南 — 第 1-3 章

---

# 第 1 章：Python 基础速通（对照本项目代码学）

> 本章用 ChatBI 的真实代码来教 Python，不是纯理论。每个概念都引用项目中的实际文件和行号。

---

## 1.1 类型注解 — 从 config.py 的 Settings 说起

Python 3.6+ 引入了**类型注解**（Type Hints），让你在变量和函数上标注期望的类型。Python 不会强制执行这些类型，但 IDE 和静态检查工具（如 mypy）会用它们来发现 bug。

### 基本语法

```python
name: str = "Alice"       # 变量 name 是字符串
age: int = 30             # 变量 age 是整数
score: float = 95.5       # 变量 score 是浮点数
is_active: bool = True    # 变量 is_active 是布尔值
```

### 在我们的项目里

打开 `backend/app/core/config.py`，第 59 行开始：

```python
app_env: str = "development"   # 运行环境
app_port: int = 8000           # 应用端口号
secret_key: str                # JWT 密钥（没有默认值 = 必填项）
bcrypt_rounds: int = 12        # bcrypt 哈希轮数
```

每个配置项都有类型注解：
- `app_env: str` — 告诉 Python 和 IDE：这个值应该是字符串
- `app_port: int` — 这个值应该是整数
- `secret_key: str`（没有 `= 默认值`）— 必填项，启动时如果环境变量没设置就会报错

**Pydantic 的威力**：`Settings` 继承自 `BaseSettings`，Pydantic 会自动做类型转换和校验。比如环境变量 `APP_PORT=8000` 是字符串，Pydantic 会自动转成 `int`。如果设了 `APP_PORT=abc`，启动时就会报错。

### Optional 类型

```python
from typing import Optional

lock_until: Optional[datetime] = None
```

`Optional[datetime]` 等价于 `datetime | None`，表示这个值**可以是 datetime，也可以是 None**。

在 `backend/app/db/models.py` 第 101 行：
```python
lock_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
```
`nullable=True` 对应数据库的 NULL，`Optional[datetime]` 对应 Python 的 None。

### 动手练习

> 打开 `backend/app/core/config.py`，找到 `Settings` 类，数一数有多少个 `str` 类型的配置项、多少个 `int` 类型的。试着理解为什么 `secret_key` 没有默认值。

---

## 1.2 async/await — 为什么到处都是 async def？

Python 的 `async/await` 是**异步编程**的语法，让程序在等待 I/O（数据库查询、网络请求）时不阻塞，可以同时处理其他请求。

### 核心概念

```python
# 同步函数 — 调用时立即执行，等待完成后返回
def get_user(email):
    user = db.query(User).filter(email=email).first()  # 阻塞等待数据库
    return user

# 异步函数 — 调用时返回一个"协程"对象，需要 await 才能拿到结果
async def get_user(email):
    result = await db.execute(select(User).where(User.email == email))  # 非阻塞等待
    return result.scalar_one_or_none()
```

**关键规则**：
1. `async def` 定义异步函数
2. `await` 只能在 `async def` 函数里使用
3. `await` 暂停当前函数，把控制权交给事件循环，等结果回来再继续

### 在我们的项目里

打开 `backend/app/api/auth.py`，第 87 行：

```python
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # 检查邮箱是否已被注册 — await 等待数据库查询完成
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(...)

    # 创建租户 — await 等待数据库写入
    tenant = Tenant(name=f"user-{uuid.uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()  # flush() 发送 SQL 但不提交

    # 创建用户 — await 等待数据库写入
    user = User(...)
    db.add(user)
    await db.commit()  # commit() 提交事务
```

为什么用异步？因为 ChatBI 是 Web 服务，同时可能有几十个用户在查询。如果用同步，一个用户的数据库查询会阻塞所有其他用户。用异步后，等待数据库的时间可以用来处理其他请求。

### async with 和 async for

```python
# async with — 异步上下文管理器，确保资源自动释放
async with async_session_factory() as session:
    # session 在这个代码块结束后自动关闭
    result = await session.execute(query)

# async for — 异步迭代器，逐条读取大量数据
async for row in await conn.stream(query):
    process(row)
```

在 `backend/app/db/session.py` 第 27 行：
```python
async def get_db():
    async with async_session_factory() as session:
        yield session  # yield 让 session 在请求结束后才关闭
```

### 动手练习

> 打开 `backend/app/api/auth.py`，找到 `login` 函数（约第 161 行），数一数有多少个 `await`。每个 `await` 在等什么？如果把所有 `await` 去掉，程序会怎样？

---

## 1.3 装饰器 — @router.post、@field_validator 背后的原理

**装饰器**（Decorator）是 Python 的高级特性：用一个 `@` 符号把函数"包装"起来，在不修改原函数代码的情况下增加功能。

### 基本概念

```python
# 定义一个装饰器
def log_call(func):
    def wrapper(*args, **kwargs):
        print(f"调用函数: {func.__name__}")
        result = func(*args, **kwargs)
        print(f"函数返回: {result}")
        return result
    return wrapper

# 使用装饰器
@log_call
def add(a, b):
    return a + b

add(1, 2)  # 输出: 调用函数: add → 函数返回: 3
```

`@log_call` 等价于 `add = log_call(add)` — 把原函数替换成包装后的函数。

### 在我们的项目里

**1. @router.post — FastAPI 路由装饰器**

打开 `backend/app/api/auth.py`，第 82-86 行：

```python
@router.post(
    "/register",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
```

`@router.post("/register", ...)` 做了什么？
- 把 `register` 函数注册为 POST `/auth/register` 的处理函数
- `response_model=dict` — 告诉 FastAPI 返回值是字典
- `status_code=201` — 成功时返回 201（Created）而非默认的 200

**2. @field_validator — Pydantic 校验装饰器**

打开 `backend/app/core/config.py`，搜索 `field_validator`：

```python
@field_validator("bcrypt_rounds", mode="after")
def _validate_bcrypt_rounds(cls, v: int) -> int:
    if not (4 <= v <= 31):
        raise ValueError("bcrypt_rounds must be between 4 and 31")
    return v
```

`@field_validator("bcrypt_rounds", mode="after")` 做了什么？
- 在 `bcrypt_rounds` 赋值后自动触发这个函数
- 如果值不在 4-31 之间，抛出 ValueError 阻止启动
- `mode="after"` 表示在类型转换之后校验（先转 int，再校验范围）

### 动手练习

> 打开 `backend/app/api/auth.py`，找到所有 `@router.post` 和 `@router.get` 装饰器，列出每个装饰器对应的 URL 路径和 HTTP 方法。

---

## 1.4 字典推导式 & 列表推导式

**推导式**（Comprehension）是 Python 从一个序列快速创建新列表/字典的简洁语法。

### 列表推导式

```python
# 传统写法
squares = []
for i in range(10):
    squares.append(i ** 2)

# 列表推导式 — 一行搞定
squares = [i ** 2 for i in range(10)]
# 结果: [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]

# 带条件过滤
even_squares = [i ** 2 for i in range(10) if i % 2 == 0]
# 结果: [0, 4, 16, 36, 64]
```

### 字典推导式

```python
# 传统写法
user_dict = {}
for key, value in user_data.items():
    user_dict[key] = str(value)

# 字典推导式
user_dict = {key: str(value) for key, value in user_data.items()}
```

### 在我们的项目里

打开 `backend/app/api/auth.py`，第 253-258 行：

```python
token_data = {
    "user_id": str(user.id),
    "email": user.email,
    "tenant_id": str(user.tenant_id),
    "role": user.role,
}
```

这是手动构建字典。在更复杂的场景中，推导式更常见，比如在 `backend/app/ai/nodes/schema_selection.py` 中构建表结构上下文时。

### 动手练习

> 在项目代码中搜索 `[` 开头、`for` 在中间、`]` 结尾的模式，找到至少 3 个列表推导式的使用。

---

## 1.5 f-string — f"Hello {name}" 格式化

**f-string**（format string）是 Python 3.6+ 的字符串格式化语法，在字符串前加 `f`，就可以用 `{}` 插入变量。

### 基本语法

```python
name = "Alice"
age = 30

# f-string
greeting = f"Hello, {name}! You are {age} years old."
# 结果: "Hello, Alice! You are 30 years old."

# 可以在 {} 里写表达式
msg = f"Next year you'll be {age + 1}."
# 结果: "Next year you'll be 31."

# 格式化数字
pi = 3.14159
print(f"Pi is approximately {pi:.2f}")  # "Pi is approximately 3.14"
```

### 在我们的项目里

打开 `backend/app/api/auth.py`，第 125 行：

```python
tenant = Tenant(name=f"user-{uuid.uuid4().hex[:6]}")
```

拆解这个 f-string：
- `uuid.uuid4()` — 生成一个随机 UUID
- `.hex` — 转成十六进制字符串（去掉横杠）
- `[:6]` — 取前 6 个字符
- `f"user-{...}"` — 拼接成 `user-a3b2c1` 这样的租户名

### 动手练习

> 在项目代码中搜索 `f"`，找到 5 个 f-string 的使用，理解每个 `{}` 里在做什么。

---

## 1.6 dataclass vs TypedDict vs Pydantic — 三种数据结构怎么选

Python 有三种常见的"结构化数据"方式，各有适用场景：

### dataclass — Python 标准库，适合内部数据传递

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float

p = Point(1.0, 2.0)
print(p.x, p.y)  # 1.0 2.0
```

### TypedDict — 类型注解专用，适合描述字典的结构

```python
from typing import TypedDict

class UserInfo(TypedDict):
    name: str
    age: int

user: UserInfo = {"name": "Alice", "age": 30}  # IDE 会提示键名和类型
```

### Pydantic — 数据校验 + 序列化，适合 API 边界

```python
from pydantic import BaseModel

class RegisterRequest(BaseModel):
    email: str
    password: str

req = RegisterRequest(email="a@b.com", password="123")
# 自动校验 email 格式、password 强度
# 自动序列化为 JSON 响应
```

### 在我们的项目里

**TypedDict — AI 管线的状态**

打开 `backend/app/ai/state.py`：

```python
class QueryState(TypedDict):
    question: str                    # 用户原始问题
    intent: str                      # 意图分类结果
    sql: str                         # 生成的 SQL
    result: Any                      # 查询结果
    # ... 更多字段
```

LangGraph 用 TypedDict 描述节点之间传递的状态。TypedDict 不做校验，只做类型提示。

**Pydantic — API 请求/响应**

打开 `backend/app/schemas/auth.py`（如果存在）或 `backend/app/api/auth.py`：

```python
class RegisterRequest(BaseModel):
    email: EmailStr      # Pydantic 自动校验邮箱格式
    password: str        # 可加 field_validator 校验密码强度
```

FastAPI 用 Pydantic 做请求体验证和响应体序列化。

### 选择指南

| 场景 | 用什么 | 为什么 |
|------|--------|--------|
| API 请求/响应 | Pydantic BaseModel | 自动校验 + 序列化 + 文档生成 |
| 函数间传递数据 | dataclass | 轻量、标准库、有 __init__ |
| 字典结构提示 | TypedDict | 不创建新类，只标注类型 |
| LangGraph 状态 | TypedDict | 框架要求，节点间共享 |

### 动手练习

> 打开 `backend/app/ai/state.py`，看 `QueryState` 的所有字段。再打开 `backend/app/api/auth.py`，看 `RegisterRequest` 和 `TokenResponse` 的定义。对比 TypedDict 和 Pydantic 的区别。

---

## 1.7 上下文管理器 — async with、async for

**上下文管理器**（Context Manager）确保资源在使用后自动释放，无论是否发生异常。

### with 语句 — 同步版本

```python
# 不用 with — 容易忘记关闭
f = open("data.txt")
content = f.read()
f.close()  # 如果 read() 抛异常，close() 不会执行！

# 用 with — 保证关闭
with open("data.txt") as f:
    content = f.read()  # 无论是否异常，f 都会自动关闭
```

### async with 语句 — 异步版本

```python
# 异步上下文管理器 — 数据库会话
async with async_session_factory() as session:
    result = await session.execute(query)
    # 代码块结束后，session 自动关闭
```

### 在我们的项目里

打开 `backend/app/db/session.py`，第 27-29 行：

```python
async def get_db():
    async with async_session_factory() as session:
        yield session
```

这是 FastAPI 的**依赖注入**模式：
1. 请求进来时，`get_db()` 创建一个数据库会话
2. `yield session` 把会话交给路由函数使用
3. 请求结束后，`async with` 确保会话自动关闭

### 动手练习

> 在项目代码中搜索 `async with`，找到至少 3 处使用。每处在管理什么资源？

---

## 1.8 模块与包 — from app.ai.nodes import intent 是怎么找到文件的

Python 的**模块**（Module）就是一个 `.py` 文件，**包**（Package）就是包含 `__init__.py` 的目录。

### 导入规则

```
backend/
└── app/                    ← 包（有 __init__.py）
    ├── ai/                 ← 包
    │   ├── graph.py        ← 模块
    │   └── nodes/          ← 包
    │       ├── intent.py   ← 模块
    │       └── execution.py← 模块
    ├── api/                ← 包
    │   └── auth.py         ← 模块
    └── core/               ← 包
        └── config.py       ← 模块
```

```python
# 导入整个模块
from app.ai import graph

# 导入模块中的特定函数
from app.ai.graph import build_graph

# 从子包导入
from app.ai.nodes.intent import classify_intent_node

# 导入并重命名
from app.core.config import settings  # settings 是 Settings 的单例
```

### 在我们的项目里

打开 `backend/app/api/auth.py`，第 37-68 行的导入区：

```python
from app.db.session import get_db                    # 数据库会话
from app.db.models import User, Tenant               # ORM 模型
from app.core.security import hash_password          # 密码哈希
from app.core.security import create_access_token    # JWT 生成
from app.schemas.auth import RegisterRequest         # 请求体模型
from app.services.login_lock_service import check_lock  # 登录锁定
from app.api._helpers import api_error               # 共享工具函数
```

每一行导入都对应一个文件：
- `app.db.session` → `backend/app/db/session.py`
- `app.db.models` → `backend/app/db/models.py`
- `app.core.security` → `backend/app/core/security.py`
- `app.schemas.auth` → `backend/app/schemas/auth.py`
- `app.services.login_lock_service` → `backend/app/services/login_lock_service.py`
- `app.api._helpers` → `backend/app/api/_helpers.py`

### 动手练习

> 打开 `backend/app/ai/graph.py`，看它的导入区。画出导入依赖图：graph.py 导入了哪些 nodes/ 下的文件？每个 node 文件又导入了什么？

---

# 第 2 章：FastAPI 框架入门（对照 main.py + api/ 学习）

> FastAPI 是 Python 最快的 Web 框架之一，自动生成 API 文档，内置异步支持。

---

## 2.1 路由与端点 — @router.get/post 是什么

**路由**（Route）把 URL 路径和 Python 函数绑定起来。当用户访问某个 URL 时，FastAPI 调用对应的函数处理请求。

### 基本概念

```python
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["认证"])

@router.get("/me")           # GET /auth/me
async def get_me():
    return {"user": "current"}

@router.post("/login")       # POST /auth/login
async def login(req: LoginRequest):
    return {"token": "..."}
```

- `APIRouter` — 创建路由组，`prefix` 给所有路由加前缀
- `@router.get` / `@router.post` — 声明 HTTP 方法和路径
- `tags` — Swagger 文档分组

### 在我们的项目里

打开 `backend/app/main.py`，第 33-44 行：

```python
from app.api.auth import router as auth_router
from app.api.datasource import router as datasource_router
from app.api.query import router as query_router
# ... 更多路由
```

然后在 `create_app()` 中挂载：

```python
app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(datasource_router, prefix=settings.api_prefix)
app.include_router(query_router, prefix=settings.api_prefix)
```

`settings.api_prefix` 默认是 `/chat-bi/api/v1`，所以最终路径是：
- POST `/chat-bi/api/v1/auth/register`
- POST `/chat-bi/api/v1/auth/login`
- POST `/chat-bi/api/v1/query`

### 动手练习

> 启动后端服务，访问 `http://localhost:8999/chat-bi/api/v1/docs`，看 Swagger 自动生成的 API 文档。数一数有多少个端点。

---

## 2.2 依赖注入 — Depends(get_db)、Depends(get_current_user)

**依赖注入**（Dependency Injection）是 FastAPI 的核心机制：框架自动创建和传递函数需要的依赖，你不用手动创建。

### 基本概念

```python
async def get_db():
    async with async_session_factory() as session:
        yield session

@router.post("/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # db 由 FastAPI 自动创建和传入
    result = await db.execute(select(User))
```

`Depends(get_db)` 告诉 FastAPI："调用 register 时，先调用 get_db() 获取数据库会话，传给 db 参数。"

### 在我们的项目里

打开 `backend/app/api/auth.py`，第 87 行：

```python
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
```

- `req: RegisterRequest` — FastAPI 自动把请求体 JSON 反序列化为 RegisterRequest 对象
- `db: AsyncSession = Depends(get_db)` — FastAPI 自动创建数据库会话

更复杂的依赖：`Depends(get_current_user)` — 从 JWT 令牌中提取当前用户。

### 动手练习

> 在 `backend/app/api/` 目录下搜索 `Depends(`，统计有多少个端点使用了 `Depends(get_db)`，有多少个使用了 `Depends(get_current_user)`。

---

## 2.3 Pydantic Schema — 请求体验证、响应体序列化

Pydantic 是 FastAPI 的数据校验引擎，自动做三件事：
1. **反序列化** — 把 JSON 请求体转成 Python 对象
2. **校验** — 检查字段类型和约束
3. **序列化** — 把 Python 对象转成 JSON 响应

### 在我们的项目里

请求体（`backend/app/schemas/auth.py`）：

```python
class RegisterRequest(BaseModel):
    email: EmailStr      # 自动校验邮箱格式
    password: str        # 可加 min_length 约束
```

响应体：

```python
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    email_verified: bool
```

当 FastAPI 看到 `response_model=TokenResponse`，会自动把返回值序列化为 JSON，只包含 TokenResponse 定义的字段。

### 动手练习

> 打开 `backend/app/schemas/` 目录，看有哪些 Schema 文件。对比请求 Schema 和响应 Schema 的区别。

---

## 2.4 中间件 — CORS、安全头、限流

**中间件**（Middleware）是在每个请求前后执行的代码，像洋葱一样层层包裹。

### 在我们的项目里

打开 `backend/app/main.py`，搜索 `add_middleware`：

```python
# CORS — 允许前端跨域访问
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, ...)

# 安全头 — 给每个响应加安全头（X-Content-Type-Options 等）
app.add_middleware(SecurityHeadersMiddleware)

# 限流 — 防止暴力请求
app.add_middleware(ASGIMiddleware, middleware=rate_limit_middleware)
```

**关键**：中间件执行顺序与注册顺序**相反**！最后注册的最先执行。所以请求进来时：限流 → 安全头 → CORS → 路由。

### 动手练习

> 打开 `backend/app/main.py`，找到所有 `add_middleware` 调用。画一个请求从进来到路由的中间件执行顺序图。

---

## 2.5 SSE 流式响应 — yield + StreamingResponse

**SSE**（Server-Sent Events）让服务器能持续向客户端推送数据，ChatBI 用它来实时显示 AI 推理过程。

### 核心模式

```python
from fastapi.responses import StreamingResponse

async def event_generator():
    yield f"data: {json.dumps({'step': 'intent'})}\n\n"   # 第一步：意图识别
    yield f"data: {json.dumps({'step': 'schema'})}\n\n"   # 第二步：Schema 选择
    yield f"data: {json.dumps({'step': 'result'})}\n\n"   # 第三步：返回结果

@router.post("/query")
async def query(req: QueryRequest):
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

`yield` 是 Python 的**生成器**语法：每次 `yield` 产出一个值，函数暂停，等下次调用再继续。

### 在我们的项目里

打开 `backend/app/api/query.py`，搜索 `StreamingResponse`：

```python
return StreamingResponse(
    execute_query_pipeline(...),
    media_type="text/event-stream",
)
```

`execute_query_pipeline()` 是一个 async generator，每完成一个 AI 步骤就 yield 一个事件。

### 动手练习

> 打开 `backend/app/services/pipeline_executor.py`，找到 `execute_query_pipeline()` 函数，看它 yield 了哪些事件类型。

---

## 2.6 后台任务 — BackgroundTasks

FastAPI 的 `BackgroundTasks` 让你在返回响应后异步执行任务，不阻塞用户。

```python
from fastapi import BackgroundTasks

@router.post("/sync")
async def sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sync_job, datasource_id)
    return {"status": "started"}  # 立即返回，sync 在后台执行
```

### 动手练习

> 在 `backend/app/api/` 目录下搜索 `BackgroundTasks`，看哪些端点使用了后台任务。

---

## 2.7 lifespan — 应用启动/关闭的生命周期

**lifespan** 是 FastAPI 的应用生命周期管理，替代旧版的 `on_startup` / `on_shutdown` 事件。

### 在我们的项目里

打开 `backend/app/main.py`，第 54 行开始：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动阶段 ──
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # 自动建表
    # ... 清缓存、启动定时任务

    yield  # ← 应用运行中

    # ── 关闭阶段 ──
    await pool_manager.close_all()  # 释放数据库连接池
    await close_redis()              # 释放 Redis 连接
```

`yield` 是分界线：之前是启动逻辑，之后是关闭逻辑。这和 `get_db()` 的 `yield session` 是同一个模式。

### 动手练习

> 打开 `backend/app/main.py`，在 `lifespan` 函数中，列出启动时做了哪些事、关闭时做了哪些事。

---

# 第 3 章：SQLAlchemy ORM（对照 db/models.py 学习）

> SQLAlchemy 是 Python 最流行的 ORM（对象关系映射），用 Python 类描述数据库表，不用手写 SQL。

---

## 3.1 声明式模型 — class User(Base)

ORM 的核心思想：**一个 Python 类 = 一张数据库表**。

### 在我们的项目里

打开 `backend/app/db/models.py`，第 47 行：

```python
class Tenant(Base):
    __tablename__ = "tenants"  # 对应数据库中的表名

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
```

- `class Tenant(Base)` — 继承 `Base`，SQLAlchemy 就知道这是一个模型类
- `__tablename__ = "tenants"` — 对应数据库的表名
- `id`、`name`、`created_at` — 类属性 = 表字段

### 和原始 SQL 的对比

```sql
-- 手写 SQL 建表
CREATE TABLE tenants (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    created_at DATETIME DEFAULT NOW()
);
```

```python
# SQLAlchemy ORM 建表
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
```

ORM 的好处：Python 代码和数据库自动同步，不用维护两套定义。

### 动手练习

> 打开 `backend/app/db/models.py`，数一数有多少个 `class XXX(Base)` 定义。每个类对应哪张表？

---

## 3.2 Mapped 类型注解 — id: Mapped[uuid.UUID]

SQLAlchemy 2.0 引入了 `Mapped[type]` 语法，同时声明 Python 类型和数据库列类型。

### 语法对照

```python
# SQLAlchemy 1.x（旧版）
id = Column(Integer, primary_key=True)
name = Column(String(200), nullable=False)

# SQLAlchemy 2.0（新版，本项目使用）
id: Mapped[int] = mapped_column(primary_key=True)
name: Mapped[str] = mapped_column(String(200), nullable=False)
```

`Mapped[str]` 表示：
- Python 侧：这个属性是 `str` 类型
- 数据库侧：对应的列是 `VARCHAR`

### 在我们的项目里

打开 `backend/app/db/models.py`，第 82-90 行（User 模型）：

```python
id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
```

- `Mapped[uuid.UUID]` — Python 中是 UUID 对象，数据库中是 CHAR(36)（通过 GUID 自定义类型）
- `index=True` — 创建索引，加速查询
- `unique=True` — 唯一约束，不能重复

### 动手练习

> 打开 `backend/app/db/models.py`，找到 `DataSource` 类，列出每个字段的 `Mapped` 类型和数据库约束。

---

## 3.3 关系与外键 — tenant_id 关联

**外键**（Foreign Key）建立表与表之间的关系。ChatBI 用 `tenant_id` 实现多租户数据隔离。

### 在我们的项目里

```python
class User(Base):
    __tablename__ = "users"
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
```

`tenant_id` 指向 `tenants` 表的 `id` 字段。虽然这里没有显式写 `ForeignKey`（项目用应用层保证一致性），但逻辑上每个用户都属于一个租户。

查询时的关联：

```python
# 查询某个租户下的所有用户
result = await db.execute(
    select(User).where(User.tenant_id == tenant_id)
)
```

### 动手练习

> 在 `backend/app/db/models.py` 中，找出所有包含 `tenant_id` 的模型。这就是多租户隔离的"数据边界"。

---

## 3.4 异步 Session — async_session_factory、get_db

SQLAlchemy 的 **Session** 是与数据库交互的入口。本项目使用异步版本。

### 在我们的项目里

打开 `backend/app/db/session.py`：

```python
# 1. 创建异步引擎 — 连接数据库
engine = create_async_engine(
    settings.database_url,       # 数据库连接字符串
    echo=settings.app_env == "development",  # 开发环境打印 SQL
    pool_size=5,                 # 连接池大小
    max_overflow=10,             # 最多额外创建 10 个连接
)

# 2. 创建会话工厂 — 生成会话
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,      # commit 后对象不过期
)

# 3. 依赖注入函数 — FastAPI 每个请求调用一次
async def get_db():
    async with async_session_factory() as session:
        yield session
```

数据流：

```
请求进来 → FastAPI 调用 get_db() → 创建 Session → 传给路由函数 → 路由执行完毕 → Session 自动关闭
```

### 动手练习

> 打开 `backend/app/db/session.py`，理解 `pool_size` 和 `max_overflow` 的含义。如果同时有 20 个请求，连接池会怎样？

---

## 3.5 GUID 自定义类型 — 跨数据库 UUID

MySQL 没有原生 UUID 类型，PostgreSQL 有。ChatBI 的 `GUID` 类型自动适配两种数据库。

### 在我们的项目里

打开 `backend/app/db/types.py`：

```python
class GUID(TypeDecorator):
    """Platform-independent GUID type."""
    impl = CHAR(32)              # MySQL: 用 CHAR(32) 存储（无横杠的 hex）
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID())  # PostgreSQL: 用原生 UUID
        else:
            return dialect.type_descriptor(CHAR(32))  # MySQL/SQLite: 用 CHAR(32)
```

这样写的好处：同一套模型代码，在 MySQL 和 PostgreSQL 上都能用，不需要改模型定义。

### 动手练习

> 打开 `backend/app/db/types.py`，看 `GUID` 的 `process_bind_param` 和 `process_result_value` 方法。理解 UUID 在写入和读取时如何转换。
## 第 4 章：LangGraph 核心概念（对照 ai/graph.py 学习）

本章用 ChatBI 的真实代码带你理解 LangGraph 的 7 个核心概念。学完后，你将看懂
`backend/app/ai/graph.py` 的每一行，并理解"为什么 ChatBI 用 LangGraph 而不是普通函数调用"。

---

### 4.1 什么是 LangGraph — 为什么不用普通函数调用

#### 概念

假设你要实现"用户提问 → 意图识别 → Schema 选择 → SQL 生成 → 执行"这条管线，
最直觉的写法是函数嵌套调用：

```python
# 普通函数调用写法（ChatBI 没有采用）
intent = classify_intent(question)
schema = schema_selection(question, intent)
sql = generate_sql(question, schema)
result = execute_sql(sql)
```

这种写法有三个问题：

| 问题 | 说明 | LangGraph 的解决方案 |
|------|------|---------------------|
| **硬编码流程** | 调用顺序写死在代码里，加一个节点要改多处 | 图结构声明式定义，加节点只需 add_node + add_edge |
| **条件分支难扩展** | if/else 嵌套，分支越多越难读 | 条件边 add_conditional_edges，路由函数返回节点名 |
| **状态传递混乱** | 每个函数的参数和返回值各不相同，改一个要改上下游 | 统一的 State 对象，节点只返回增量，自动合并 |

LangGraph 把管线建模为一张**有向图**（Directed Graph）：
- **节点**（Node）= 处理步骤（一个 async 函数）
- **边**（Edge）= 数据流向（普通边 or 条件边）
- **状态**（State）= 所有节点共享的数据字典

ChatBI 的图结构如下：

```
classify_intent ──→ (DataQuery?) ──→ resolve_context ──→ schema_selection
               │                                          │
               │(非DataQuery)                             │
               ↓                                          ↓
           misleading                                generate_sql
               │                                          │
               ↓                                          ↓
              END                                    execute_sql
                                                          │
                                                          ↓
                                                         END
```

#### 动手练习

1. 对比两种写法：用普通函数实现一个 3 步管线（A→B→C），然后改用 LangGraph 的 StateGraph 实现，体会声明式定义的好处。

2. 在 `graph.py` 的 `build_graph()` 末尾加一行 `print(graph.nodes)`，观察注册了哪些节点。

---

### 4.2 StateGraph — 状态图的定义

#### 概念

`StateGraph` 是 LangGraph 提供的核心类，用来定义一张"状态图"。你可以把它理解为一张空白画布——
你在画布上添加节点（工位）和边（传送带），最后编译成一条可执行的流水线。

创建 StateGraph 时需要指定**状态类型**（泛型参数），这告诉 LangGraph："这张图上流动的数据是什么格式"。

#### 真实代码

`backend/app/ai/graph.py:146`：

```python
graph = StateGraph(QueryState)
```

#### 逐行解读

1. **`StateGraph`** — 从 `langgraph.graph` 导入的类（第 46 行：`from langgraph.graph import StateGraph, END`）。
   它是 LangGraph 的图容器，提供 `add_node`、`add_edge`、`add_conditional_edges`、`compile` 等方法。
2. **`QueryState`** — 泛型参数，指定状态类型。`QueryState` 是一个 `TypedDict`（详见 4.3 节），
   定义了所有节点共享的字段名和类型。LangGraph 会根据这个类型校验节点的输入输出。
3. **`graph = StateGraph(QueryState)`** — 此时 graph 是一张空白画布，还没有任何节点和边。
   后续的 `add_node`、`add_edge` 等调用逐步填充这张画布。

#### 类比

```
StateGraph(QueryState)  ≈  画布（规定了画布上流动的颜料类型是 QueryState）
add_node(...)           ≈  在画布上画一个工位
add_edge(...)           ≈  用传送带连接两个工位
compile()               ≈  把画布封膜，变成可运行的流水线
```

#### 动手练习

1. 在 Python REPL 中创建一个最简 StateGraph：

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END

class MyState(TypedDict, total=False):
    value: int

g = StateGraph(MyState)
print(g)  # <langgraph.graph.state.StateGraph object at ...>
```

---

### 4.3 State (TypedDict) — 节点之间共享的状态对象

#### 概念

在 LangGraph 中，**状态（State）** 是所有节点之间传递数据的唯一载体，类似于一条流水线上的传送带：
每个节点从传送带上读取数据、处理后把结果放回传送带。

ChatBI 用 Python 的 `TypedDict` 定义状态，这是一种轻量级的类型提示工具——运行时仍然是普通 dict，
零性能开销，但 IDE 和 mypy 能帮你检查字段名拼错。

#### 真实代码

`backend/app/ai/state.py:64`：

```python
class QueryState(TypedDict, total=False):
    """ChatBI 查询状态 —— LangGraph 图中所有节点共享的数据结构。"""

    # ── 输入组：由 API 层设置，整个流程的起点 ──
    question: str              # 用户的自然语言问题
    datasource_id: str         # 数据源 ID
    tenant_id: str             # 租户 ID
    conversation_history: list[dict]  # 对话历史

    # ── 理解组：由意图分类和 Schema 选择节点设置 ──
    intent: str                # 意图分类结果："DataQuery" 或 "Other"
    schema_context: str        # LLM 精选后的数据库表结构描述文本
    raw_metadata: str          # 数据源的完整元数据 JSON 字符串

    # ── 生成组：由 SQL 生成节点设置 ──
    sql: str                   # 生成的 SQL 查询语句
    table_fixes: list[str]     # SQL 表名自动修复记录
    column_fixes: list[str]    # SQL 列名自动修复记录

    # ── 结果组：由 SQL 执行节点设置 ──
    success: bool              # 查询是否成功
    error: str                 # 错误信息
    columns: list[str]         # 查询结果的列名列表
    rows: list[dict]           # 查询结果的数据行
    row_count: int             # 结果行数
    execution_time_ms: int     # SQL 执行耗时（毫秒）
    chart_type: str            # 推断的图表类型
```

#### 逐行解读

1. **`TypedDict`** — Python 标准库 `typing` 模块提供的类型提示工具。与 `dataclass` / `Pydantic Model` 的区别：
   - `TypedDict`：轻量级，只做类型提示，运行时仍然是普通 dict，零性能开销
   - `dataclass`：会生成 `__init__` 等方法，适合业务对象
   - `Pydantic Model`：带运行时校验，适合 API 输入输出
   LangGraph 选择 TypedDict 是因为状态本质上就是一个字典，不需要实例化、不需要校验。

2. **`total=False`** — 表示"不是所有字段都必须存在"。这对 LangGraph 至关重要：
   图的入口节点只设置 `question` 等少量字段，后续节点逐步补充字段（如 `intent`、`sql`、`rows` 等）。
   如果 `total=True`（默认），Python 类型检查器会要求创建时提供所有字段，这在 LangGraph 中是不现实的。

3. **字段分组** — 注释把字段分为四组（输入组、理解组、生成组、结果组），对应 AI 管线的四个阶段。
   这不是 Python 语法要求，而是代码组织约定，帮助读者理解数据流转。

4. **节点只返回增量** — 每个节点不需要返回完整的 QueryState，只需返回要修改的字段。
   例如 `intent_node` 只返回 `{"intent": "DataQuery"}`，LangGraph 会自动把这个增量合并到全局状态中。

#### 状态流转过程

```
classify_intent        读取: question
                       写入: intent

resolve_context        读取: question, conversation_history
                       写入: question（可能被补全）

schema_selection       读取: question, datasource_id, tenant_id
                       写入: schema_context, raw_metadata

generate_sql           读取: question, schema_context, raw_metadata
                       写入: sql, table_fixes, column_fixes

execute_sql            读取: sql, datasource_id, tenant_id, schema_context
                       写入: success, error, columns, rows, row_count, execution_time_ms, chart_type
```

#### 动手练习

1. 在 Python REPL 中创建一个 QueryState 实例，观察它就是普通 dict：

```python
from app.ai.state import QueryState
state: QueryState = {"question": "上个月销售额是多少？", "datasource_id": "ds-001", "tenant_id": "t-001"}
print(state["question"])  # 上个月销售额是多少？
print(state.get("intent", "未设置"))  # 未设置（intent 还没被设置，用 get 安全取值）
```

2. 模拟 LangGraph 的状态合并：节点返回增量，手动合并到全局状态。

```python
state = {"question": "上个月销售额", "datasource_id": "ds-001"}
increment = {"intent": "DataQuery"}  # intent_node 的返回值
state.update(increment)  # LangGraph 内部做的合并操作
print(state)  # {'question': '上个月销售额', 'datasource_id': 'ds-001', 'intent': 'DataQuery'}
```

---

### 4.4 Node — 每个节点是一个 async 函数

#### 概念

LangGraph 的**节点**（Node）是图中的每个处理步骤，本质上就是一个接收状态、返回状态增量的异步函数。

约定：
- **输入**：当前全局状态（QueryState 类型）
- **输出**：一个字典，包含要修改的字段（增量），LangGraph 自动合并到全局状态

ChatBI 的 `graph.py` 中定义了 6 个节点函数，全部在 `build_graph()` 内部定义（延迟导入，避免模块加载时初始化 LLM）。

#### 真实代码

**意图分类节点** — `backend/app/ai/graph.py:154-158`：

```python
async def intent_node(state: QueryState) -> dict:
    intent = await classify_intent(state["question"])
    return {"intent": intent}
```

**上下文补全节点** — `backend/app/ai/graph.py:169-177`：

```python
async def resolve_context_node(state: QueryState) -> dict:
    from app.ai.nodes.context_resolver import resolve_context
    history = state.get("conversation_history", [])
    resolved = resolve_context(state["question"], history)
    if resolved != state["question"]:
        return {"question": resolved}
    return {}
```

**SQL 执行节点** — `backend/app/ai/graph.py:205-247`：

```python
async def execution_node(state: QueryState) -> dict:
    from app.ai.chart_type import infer_chart_type
    from app.ai.nodes.self_heal import self_heal_sql

    result = await execute_sql(state["sql"], state["datasource_id"], tenant_id=state.get("tenant_id"))
    final_sql = state["sql"]

    if not result["success"] and state.get("schema_context"):
        heal_result = await self_heal_sql(
            question=state["question"],
            sql=state["sql"],
            error=result.get("error", ""),
            datasource_id=state.get("datasource_id", ""),
            schema_context=state["schema_context"],
            dialect="mysql",
        )
        if heal_result.get("success"):
            final_sql = heal_result.get("sql", final_sql)
            result = await execute_sql(final_sql, state["datasource_id"], tenant_id=state.get("tenant_id"))

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    chart_type = "none"
    if rows and columns:
        chart_type = infer_chart_type(columns, rows)
    return {
        "sql": final_sql,
        "success": result["success"],
        "error": result.get("error"),
        "columns": columns,
        "rows": rows,
        "row_count": result.get("row_count", 0),
        "execution_time_ms": result.get("execution_time_ms"),
        "chart_type": chart_type,
    }
```

**注册节点到图中** — `backend/app/ai/graph.py:252-257`：

```python
graph.add_node("classify_intent", intent_node)
graph.add_node("resolve_context", resolve_context_node)
graph.add_node("schema_selection", schema_node)
graph.add_node("generate_sql", generation_node)
graph.add_node("execute_sql", execution_node)
graph.add_node("misleading", handle_misleading)
```

#### 逐行解读

1. **`async def intent_node(state: QueryState) -> dict:`** — 声明一个异步函数，接收 QueryState，返回 dict。
   `async` 表示内部可以用 `await` 等待其他异步操作（如 LLM API 调用）。

2. **`await classify_intent(state["question"])`** — `await` 等待异步操作完成。
   `classify_intent` 内部会调用 LLM API，这是一个网络请求，所以是异步的。
   `await` 让出控制权，等 API 返回后再继续。

3. **`return {"intent": intent}`** — 只返回增量！不需要返回完整状态。
   LangGraph 会自动把 `{"intent": "DataQuery"}` 合并到全局状态中。

4. **`state.get("conversation_history", [])`** — 用 `.get()` 而非 `state["conversation_history"]`，
   因为 `conversation_history` 可能还没被设置（首轮对话为空）。`.get()` 在键不存在时返回默认值 `[]` 而非抛 `KeyError`。

5. **`if resolved != state["question"]: return {"question": resolved}`** — 只有当问题被改写时才返回增量，
   否则返回空字典 `{}`（表示无变更，LangGraph 不会修改任何字段）。

6. **`graph.add_node("classify_intent", intent_node)`** — 把函数注册为图中的节点。
   第一个参数是节点名称（字符串），后续用这个名称来设置边；第二个参数是处理函数。

7. **节点函数定义在 `build_graph()` 内部** — 而非模块顶层，原因是：
   (1) 避免模块导入时触发 LangChain/OpenAI 的初始化（延迟导入）；
   (2) 每次调用 `build_graph()` 都能拿到最新的配置。

#### 动手练习

1. 写一个最简的 LangGraph 节点：

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END

class MyState(TypedDict, total=False):
    value: int
    doubled: int

async def double_node(state: MyState) -> dict:
    return {"doubled": state["value"] * 2}

g = StateGraph(MyState)
g.add_node("double", double_node)
g.set_entry_point("double")
g.add_edge("double", END)
graph = g.compile()

import asyncio
result = asyncio.run(graph.ainvoke({"value": 21}))
print(result)  # {'value': 21, 'doubled': 42}
```

---

### 4.5 Edge — 普通边和条件边

#### 概念

**边**（Edge）定义节点之间的连接和数据流向。LangGraph 提供两种边：

| 边类型 | 方法 | 含义 | 类比 |
|--------|------|------|------|
| 普通边 | `add_edge(A, B)` | A 完成后一定到 B | 传送带：A 的产出直接送到 B |
| 条件边 | `add_conditional_edges(A, router, map)` | A 完成后根据状态决定走哪条路 | 分叉口：根据信号灯选择方向 |

#### 真实代码

**设置入口节点** — `backend/app/ai/graph.py:263`：

```python
graph.set_entry_point("classify_intent")
```

**条件边：意图路由** — `backend/app/ai/graph.py:273-277`：

```python
graph.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    {"schema_selection": "resolve_context", "misleading": "misleading"},
)
```

**路由函数** — `backend/app/ai/graph.py:53-73`：

```python
def route_by_intent(state: QueryState) -> str:
    if state.get("intent") == "DataQuery":
        return "schema_selection"
    return "misleading"
```

**普通边：线性流转** — `backend/app/ai/graph.py:281-283`：

```python
graph.add_edge("resolve_context", "schema_selection")
graph.add_edge("schema_selection", "generate_sql")
graph.add_edge("generate_sql", "execute_sql")
```

**终止边** — `backend/app/ai/graph.py:287-288`：

```python
graph.add_edge("misleading", END)
graph.add_edge("execute_sql", END)
```

#### 逐行解读

1. **`graph.set_entry_point("classify_intent")`** — 设置图的入口节点，数据从这里开始流动。
   相当于流水线的第一个工位。每个 StateGraph 只能有一个入口。

2. **`graph.add_conditional_edges("classify_intent", route_by_intent, {...})`** — 添加条件边，三个参数：
   - `"classify_intent"` — 从哪个节点出发
   - `route_by_intent` — 路由函数，接收当前状态，返回下一个节点的名称（字符串）
   - `{"schema_selection": "resolve_context", "misleading": "misleading"}` — 路由映射表

3. **路由映射表的巧妙设计** — `route_by_intent` 返回 `"schema_selection"`，但映射表把它映射到 `"resolve_context"`。
   为什么？因为我们需要在 schema_selection 之前先做上下文补全。路由函数返回的是"逻辑意图"，
   映射表把它翻译成"实际目标节点"。

4. **`if state.get("intent") == "DataQuery": return "schema_selection"`** — 路由函数的决策逻辑：
   读取状态中的 `intent` 字段，如果是 `"DataQuery"` 就走数据查询路径，否则走误导处理路径。

5. **`graph.add_edge("resolve_context", "schema_selection")`** — 普通边：resolve_context 完成后一定到 schema_selection。
   这四个节点形成一条线性链：resolve_context → schema_selection → generate_sql → execute_sql。

6. **`graph.add_edge("misleading", END)`** — 终止边：misleading 节点完成后，流程结束。
   `END` 是 LangGraph 内置的结束标记（从 `langgraph.graph` 导入）。

#### ChatBI 的完整边结构

```
                    ┌──────────────────────────────────────────────┐
                    │              LangGraph 边结构                 │
                    └──────────────────────────────────────────────┘

  set_entry_point("classify_intent")
                    │
                    ▼
            ┌───────────────┐
            │ classify_intent│
            └───────┬───────┘
                    │
       add_conditional_edges ──── route_by_intent(state)
                    │
          ┌─────────┴──────────┐
          │                    │
    intent=="DataQuery"    intent!="DataQuery"
          │                    │
          ▼                    ▼
  ┌─────────────────┐   ┌────────────┐
  │ resolve_context │   │ misleading │
  └────────┬────────┘   └──────┬─────┘
           │                   │
  add_edge │              add_edge │
           ▼                   ▼
  ┌─────────────────┐        END
  │ schema_selection │
  └────────┬────────┘
           │
  add_edge │
           ▼
  ┌─────────────────┐
  │  generate_sql    │
  └────────┬────────┘
           │
  add_edge │
           ▼
  ┌─────────────────┐
  │  execute_sql     │
  └────────┬────────┘
           │
  add_edge │
           ▼
          END
```

#### 动手练习

1. 修改 `route_by_intent` 函数，添加第三种意图（如 "HelpQuery"），并注册对应的节点和边。

2. 在 `build_graph()` 中把 `add_edge("resolve_context", "schema_selection")` 注释掉，运行测试观察 LangGraph 报什么错（孤立节点）。

---

### 4.6 compile() — 编译为可执行的 Runnable

#### 概念

`compile()` 是 StateGraph 的最后一步，把声明式的图定义编译为可执行对象。
编译过程会校验图的完整性（如：是否有孤立节点、是否有死循环等），
编译后的对象可以调用 `.invoke(state)` 同步执行或 `.ainvoke(state)` 异步执行。

#### 真实代码

`backend/app/ai/graph.py:293`：

```python
return graph.compile()
```

#### 逐行解读

1. **`graph.compile()`** — 编译状态图，返回一个 `CompiledGraph` 对象。
   编译过程做了什么？
   - 校验所有节点都有边连接（没有孤立节点）
   - 校验条件边的路由函数返回值都在映射表中
   - 构建内部执行计划（确定节点的执行顺序）
   - 返回可执行对象

2. **`return`** — `build_graph()` 函数的返回值就是编译后的图对象。
   调用方（`app/api/query.py`）拿到这个对象后，调用 `.ainvoke(state)` 启动整个流程。

#### 编译前 vs 编译后

```
编译前（StateGraph）           编译后（CompiledGraph）
├── nodes: {name: func}       ├── 可调用 .invoke(state)
├── edges: {(from, to)}       ├── 可调用 .ainvoke(state)
└── conditional_edges         └── 内部执行计划已构建

不能直接执行                     可以执行
```

#### 动手练习

1. 故意创建一个有孤立节点的图，观察 `compile()` 报什么错：

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END

class S(TypedDict, total=False):
    x: int

g = StateGraph(S)
g.add_node("a", lambda s: {"x": 1})
g.add_node("b", lambda s: {"x": 2})  # b 没有任何边连接
g.set_entry_point("a")
g.add_edge("a", END)
# g.compile()  # 取消注释观察报错
```

---

### 4.7 ainvoke() — 异步执行整条管线

#### 概念

`ainvoke()` 是编译后图对象的异步执行方法，传入初始状态，自动按图的定义依次执行所有节点，
返回最终状态。这是整个 LangGraph 管线的启动入口。

#### 真实代码

在 `backend/app/api/query.py` 中（调用 `build_graph()` 的地方），用法类似：

```python
graph = build_graph()
result = await graph.ainvoke({
    "question": "上个月销售额是多少？",
    "datasource_id": "ds-001",
    "tenant_id": "t-001",
    "conversation_history": [],
})
# result 是最终的 QueryState，包含所有节点的输出
print(result["sql"])         # "SELECT SUM(amount) AS '销售额' FROM t_orders WHERE ..."
print(result["chart_type"])  # "metric"
print(result["rows"])        # [{"销售额": 150000}]
```

#### 逐行解读

1. **`graph = build_graph()`** — 调用 `build_graph()` 构建并编译图对象。
   这个操作比较重（创建 LLM 实例、校验图结构），通常在应用启动时做一次，后续复用。

2. **`await graph.ainvoke({...})`** — 异步执行整条管线。
   - 传入初始状态字典（只需设置输入组字段：question, datasource_id, tenant_id）
   - LangGraph 自动从入口节点开始，按边的定义依次执行每个节点
   - 每个节点返回的增量自动合并到全局状态
   - 遇到 END 节点时停止，返回最终状态

3. **`result["sql"]`** — 最终状态包含了所有节点的输出，可以直接按字段名取值。

#### 执行过程追踪

```
ainvoke({"question": "上月销售额", "datasource_id": "ds-001", ...})
    │
    ▼
classify_intent(state) → {"intent": "DataQuery"}
    │  state 合并后: {..., "intent": "DataQuery"}
    │
    ▼  route_by_intent(state) → "schema_selection" → 映射到 "resolve_context"
resolve_context(state) → {}  (问题无需补全)
    │
    ▼
schema_selection(state) → {"schema_context": "表名: t_orders\n字段: ...", "raw_metadata": "..."}
    │
    ▼
generate_sql(state) → {"sql": "SELECT SUM(amount) AS '销售额' FROM t_orders WHERE ...", "table_fixes": [], "column_fixes": []}
    │
    ▼
execute_sql(state) → {"success": True, "columns": ["销售额"], "rows": [{"销售额": 150000}], "chart_type": "metric", ...}
    │
    ▼
END → 返回最终 state
```

#### 动手练习

1. 在 `build_graph()` 返回前加一行日志，记录图的节点和边：

```python
logger.info("Graph built: nodes=%s", list(graph.nodes))
return graph.compile()
```

2. 用 `ainvoke` 执行一个非数据查询问题，观察条件边如何路由到 `misleading` 节点：

```python
result = await graph.ainvoke({"question": "你好", "datasource_id": "ds-001", "tenant_id": "t-001"})
print(result["error"])  # "抱歉，我无法理解您的问题。请尝试提出与数据查询相关的问题..."
```

---

## 第 5 章：AI 查询管线全流程解读（逐文件带读）

本章逐文件解读 ChatBI AI 查询管线的每一个环节，从用户提问到返回图表。
每个小节对应一个源文件，包含真实代码和逐行解读。学完后，你将理解整条管线的每一个决策点。

---

### 5.1 用户提问 → graph.py 入口

#### 概念

用户在前端输入问题后，请求到达后端 `query.py` 的 SSE 路由，最终调用 `build_graph()` 构建的 LangGraph 图。
但实际生产路径走的是 `pipeline_executor.py`（第 6 章详解），它逐步 yield SSE 事件。
`graph.py` 的 `build_graph()` 是管线的"蓝图定义"，定义了节点和边的拓扑结构。

#### 真实代码

`backend/app/ai/graph.py:102-293` — `build_graph()` 函数骨架：

```python
def build_graph():
    # 延迟导入
    from app.ai.nodes.intent import classify_intent
    from app.ai.nodes.generation import generate_sql
    from app.ai.nodes.execution import execute_sql
    from app.ai.nodes.schema_selection import schema_selection_node

    # 创建状态图
    graph = StateGraph(QueryState)

    # 定义节点函数（intent_node, schema_node, resolve_context_node, ...）

    # 注册节点
    graph.add_node("classify_intent", intent_node)
    graph.add_node("resolve_context", resolve_context_node)
    graph.add_node("schema_selection", schema_node)
    graph.add_node("generate_sql", generation_node)
    graph.add_node("execute_sql", execution_node)
    graph.add_node("misleading", handle_misleading)

    # 设置边
    graph.set_entry_point("classify_intent")
    graph.add_conditional_edges("classify_intent", route_by_intent, {...})
    graph.add_edge("resolve_context", "schema_selection")
    graph.add_edge("schema_selection", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_edge("misleading", END)
    graph.add_edge("execute_sql", END)

    # 编译并返回
    return graph.compile()
```

#### 逐行解读

1. **延迟导入** — 所有 `from app.ai.nodes.xxx import` 都在函数内部而非文件顶部。
   原因：避免循环依赖，且减少启动时间（不用的节点不会被加载）。

2. **节点定义在 `build_graph()` 内部** — 如 `intent_node`、`execution_node` 等。
   这些是包装函数，内部调用 `nodes/` 目录下的实际实现。包装的目的是把实现函数的签名
   （如 `classify_intent(question)`）适配为 LangGraph 节点的统一签名（`async def node(state) -> dict`）。

3. **`handle_misleading`** — 非数据查询的兜底节点，直接返回固定提示，不调用 LLM。

#### 动手练习

1. 在 `build_graph()` 中把 `handle_misleading` 的返回值改成自定义提示，观察非数据查询问题的回复变化。

---

### 5.2 意图识别 — nodes/intent.py（关键词 + LLM + 缓存三层策略）

#### 概念

意图识别是管线的第一站，判断用户输入是"数据查询"还是"闲聊/无关问题"。
ChatBI 采用**三层策略**，按速度从快到慢依次尝试：

| 层级 | 方法 | 速度 | 准确率 | 典型场景 |
|------|------|------|--------|---------|
| 第 1 层 | Redis 缓存 | <1ms | 100%（同问题） | 用户重复提问 |
| 第 2 层 | 关键词匹配 | ~1ms | ~80% | "销售额是多少" → DataQuery |
| 第 3 层 | LLM 语义分类 | ~500ms | ~95% | "帮我看看业绩情况" → DataQuery |

#### 真实代码

**主入口函数** — `backend/app/ai/nodes/intent.py:158-239`：

```python
async def classify_intent(question: str) -> str:
    q = question.strip().lower()

    # ---- 第 1 层：Redis 缓存 ----
    try:
        redis = await get_redis()
        raw = await redis.get(_intent_cache_key(question))
        if raw:
            intent = json.loads(raw)["intent"]
            logger.info("Intent cache HIT: %s", intent)
            return intent
    except Exception:
        pass

    # ---- 第 2 层：关键词快速分类 ----
    local_result = _keyword_classify(q)
    if local_result is not None:
        logger.info("Intent (local): %s for '%s'", local_result, question[:50])
        await _intent_cache_set(question, local_result)
        return local_result

    # ---- 第 3 层：LLM 语义分类 ----
    llm = get_llm(
        max_tokens=settings.llm_intent_max_tokens,
        temperature=settings.llm_intent_temperature,
        response_format={"type": "json_object"},
    )
    messages = [
        ("system", INTENT_SYSTEM),
        ("human", question),
    ]
    try:
        async with asyncio.timeout(5):
            response = await llm.ainvoke(messages)
            raw = response.content.strip()
            data = json.loads(raw)
            intent = data.get("intent", "DataQuery")
            if intent not in ("DataQuery", "Other"):
                intent = "DataQuery"
    except Exception as e:
        logger.warning("Intent LLM failed: %s", e)
        intent = _keyword_fallback(q)

    await _intent_cache_set(question, intent)
    return intent
```

**关键词分类** — `backend/app/ai/nodes/intent.py:87-130`：

```python
def _keyword_classify(q: str) -> str | None:
    words = set(q.split())
    lower_q = q.lower()

    if words & _OTHER_KEYWORDS:          # 集合交集：命中问候词
        return "Other"

    chinese_chars = sum(1 for c in q if '一' <= c <= '鿿')
    if chinese_chars <= 2 and len(q.strip().split()) <= 1 and len(q) <= 4:
        return "Other"                   # 短问题启发式：大概率是问候

    if lower_q.startswith(("帮我", "给我", "替我")) and not (words & _QUERY_KEYWORDS):
        return "Other"

    query_match_count = sum(1 for kw in _QUERY_KEYWORDS if kw in lower_q)
    if query_match_count >= 2:           # >= 2 个查询关键词才判定
        return "DataQuery"

    return None                          # 无法判定，交给 LLM
```

#### 逐行解读

1. **`words & _OTHER_KEYWORDS`** — 集合交集运算。`words` 是问题拆分后的单词集合，
   `_OTHER_KEYWORDS` 是问候词集合（frozenset）。交集非空说明问题中包含问候词。

2. **`'一' <= c <= '鿿'`** — 判断字符是否在 CJK 统一汉字范围内。Unicode 码位 `一`=0x4E00，`鿿`=0x9FFF。

3. **`sum(1 for kw in _QUERY_KEYWORDS if kw in lower_q)`** — 统计问题中包含多少个查询关键词。
   用子串匹配（`kw in lower_q`）而非整词匹配，因为中文没有空格分词。
   `>= 2` 的阈值避免单个词误判（如"排名"可能出现在"你好，排名怎么样"中）。

4. **`asyncio.timeout(5)`** — 5 秒超时保护，防止 LLM 响应过慢阻塞整个管道。
   `async with` 是异步上下文管理器，超时后自动取消 `await` 中的协程。

5. **`response_format={"type": "json_object"}`** — 强制 LLM 输出合法 JSON。
   但仍需在 prompt 中明确说明格式，双保险。

6. **`if intent not in ("DataQuery", "Other"): intent = "DataQuery`** — 防御性校验：
   LLM 可能返回非预期的值（如 "Greeting"），强制归入合法范围。宁可多查也不漏查。

7. **`intent = _keyword_fallback(q)`** — LLM 失败时的兜底：关键词无法判定时默认归为 "DataQuery"。
   设计原则：假阴性（漏查）比假阳性（多查）代价更高。

#### 动手练习

1. 测试关键词分类的边界情况：

```python
from app.ai.nodes.intent import _keyword_classify
print(_keyword_classify("你好"))           # "Other"
print(_keyword_classify("销售额是多少"))    # "DataQuery"
print(_keyword_classify("帮我看看"))        # "Other"（"帮我"开头但无查询词）
print(_keyword_classify("帮我查销售额"))    # None（"帮我"开头但有查询词，交给 LLM）
```

2. 在 Redis CLI 中查看意图缓存：

```bash
redis-cli keys "intent:*"
```

---

### 5.3 上下文补全 — nodes/context_resolver.py（多轮对话代词解析）

#### 概念

在多轮对话中，用户经常使用代词、省略或相对时间来表达追问。如果直接把"那上个月呢？"丢给 LLM 生成 SQL，
LLM 可能无法正确理解上下文。本节点在调用 LLM 之前，先把这些模糊表达"解析"成完整问题。

#### 真实代码

**主入口函数** — `backend/app/ai/nodes/context_resolver.py:121-242`：

```python
def resolve_context(question: str, history: list[dict]) -> str:
    if not history:
        return question

    stripped = question.strip().lower()

    # 追问检测：用正则列表逐一匹配
    is_follow_up = any(re.search(pat, stripped) for pat in FOLLOW_UP_PATTERNS)

    # 极短输入（<5字符）很可能是追问
    if not is_follow_up and len(stripped) < 5:
        is_follow_up = True

    if not is_follow_up:
        return question

    # 提取上一轮有效问题
    prev_question = ""
    for h in reversed(history):
        if h.get("question") and h["question"].strip().lower() != stripped:
            prev_question = h["question"]
            break

    if not prev_question:
        return question

    # 解析相对时间引用
    resolved = _resolve_relative_time(stripped, prev_question)

    # 纯语气词追问
    if resolved.strip() in ("呢", "吧", "吗", "那呢", "那呢？", "那?", "那个呢"):
        time_words = ["去年", "今年", "上月", "本月", "上周", "本周", "昨天", "今天"]
        for tw in time_words:
            if tw in prev_question:
                for other_tw in time_words:
                    if other_tw in resolved and other_tw != tw:
                        return prev_question.replace(tw, other_tw)
        return resolved

    # 修改类追问："按X分组"拼接到上一轮问题后面
    if re.match(r"^(按|根据)", resolved):
        return f"{prev_question}，{resolved}"

    # 替换类追问："换个方式"拼接到上一轮问题后面
    if re.match(r"^(换个|另外|除了)", resolved):
        return f"{prev_question}，{resolved}"

    # 默认拼接
    if not any(re.search(pat, resolved) for pat in FOLLOW_UP_PATTERNS):
        return resolved

    return f"{prev_question}，{resolved}"
```

**相对时间解析** — `backend/app/ai/nodes/context_resolver.py:78-118`：

```python
RELATIVE_TIME = {
    "上个月": "上一个月",
    "这个月": "当前月",
    "上周": "上一周",
    "昨天": "前一天",
    "今天": "当前日",
    "去年同期": "去年同期",
    "上月": "上一个月",
    "本月": "当前月",
}

def _resolve_relative_time(question: str, prev_question: str) -> str:
    for pattern, replacement in RELATIVE_TIME.items():
        if pattern in question:
            time_words = re.findall(r"(\d{4}年|\d{1,2}月|\d{1,2}日|去年|今年|当月)", prev_question)
            if time_words:
                question = question.replace(pattern, time_words[0] + "对应的" + replacement)
    return question
```

#### 逐行解读

1. **`FOLLOW_UP_PATTERNS`** — 追问模式列表，每个元素是一个正则表达式。只要命中任意一个就判定为追问。
   包括：以"那""然后"开头的延续词、以"换个"开头的替换词、以"呢""吧"结尾的语气词等。

2. **`any(re.search(pat, stripped) for pat in FOLLOW_UP_PATTERNS)`** — `any()` + 生成器表达式，
   只要有一个正则匹配就返回 True（短路求值，不会全部匹配完）。

3. **`for h in reversed(history)`** — 从历史记录倒序查找，`reversed()` 返回反向迭代器，不创建新列表。

4. **`_resolve_relative_time(stripped, prev_question)`** — 把"上个月"替换为"2024年3月对应的上一个月"。
   从上一轮问题中提取具体时间词（如"2024年3月"），拼接到相对时间词前面，让下游 LLM 能理解具体时间范围。

5. **`if re.match(r"^(按|根据)", resolved):`** — 修改类追问：以"按"或"根据"开头，
   拼接到上一轮问题后面。例如："2024年销售额" + "，" + "按地区分组" = "2024年销售额，按地区分组"。

6. **本节点是同步函数** — `resolve_context` 不涉及 LLM 调用，只做正则匹配和字符串拼接，
   所以不需要 `async`。在 `graph.py` 中包装为 `resolve_context_node` 时也不需要 `await`。

#### 动手练习

1. 测试上下文补全的各种场景：

```python
from app.ai.nodes.context_resolver import resolve_context

# 追问补全
print(resolve_context("按地区分组呢", [{"question": "上个月销售额是多少", "answer": "..."}]))
# "上个月销售额是多少，按地区分组呢"

# 相对时间解析
print(resolve_context("那上个月呢", [{"question": "2024年3月的销售额", "answer": "..."}]))
# "2024年3月对应的上一个月呢"（或类似补全结果）
```

---

### 5.4 Schema 选择 — nodes/schema_selection.py（两步 LLM：先选表再选列）

#### 概念

数据库可能有几十甚至上百张表、上千个字段。如果把所有表结构都塞给 SQL 生成节点，
LLM 会"信息过载"，生成的 SQL 质量急剧下降。本节点用**两步 LLM 交互**解决这个问题：

```
Step 1 — 选表：用户问题 + 所有表名（只有名字和说明）→ 相关的表名列表
Step 2 — 选列：用户问题 + Step 1 选中的表的完整字段列表 → 每张表需要的字段名
```

这是 ChatBI 的核心创新点，替代了传统的 RAG 检索方式。

#### 真实代码

**节点主入口** — `backend/app/ai/nodes/schema_selection.py:377-485`：

```python
async def schema_selection_node(state: dict) -> dict:
    question = state.get("question", "")
    datasource_id = state.get("datasource_id", "")
    tenant_id = state.get("tenant_id", "")

    # 从数据库获取元数据
    async with async_session_factory() as db:
        query = select(MetadataConfig).where(
            MetadataConfig.datasource_id == datasource_id,
            MetadataConfig.tenant_id == tenant_id,
        )
        config_result = await db.execute(query)
        config = config_result.scalar_one_or_none()
        raw_metadata = config.config if config else ""

    metadata = json.loads(raw_metadata)

    # Step 1: 选择表
    selected_tables = await _select_tables(question, metadata)

    # 兜底：LLM 没选出任何表时，取前 5 张表
    if not selected_tables:
        selected_tables = [m["name"] for m in metadata.get("models", [])[:5] if not m.get("_deleted")]

    # Step 2: 选择字段
    selected_columns = await _select_columns(question, selected_tables, metadata)

    # 构建 schema context
    schema_context = _build_schema_context(selected_tables, selected_columns, metadata)

    # 附加所有表名列表（防止 LLM 幻觉）
    all_table_names = [m["name"] for m in metadata.get("models", []) if not m.get("_deleted")]
    schema_context += f"\n可用表名: {', '.join(all_table_names)}\n"

    return {
        "schema_context": schema_context,
        "raw_metadata": raw_metadata,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
    }
```

**Step 1: 选表** — `backend/app/ai/nodes/schema_selection.py:85-179`：

```python
async def _select_tables(question: str, metadata: dict) -> list[str]:
    models = metadata.get("models", [])
    rels = metadata.get("relationships", [])

    # 构建轻量表列表（只有表名+说明，不含字段）
    table_lines = []
    for m in models:
        if m.get("_deleted"):
            continue
        name = m.get("name", "?")
        desc = m.get("description", "") or m.get("comment", "") or ""
        line = f"- {name}"
        if desc:
            line += f" ({desc})"
        table_lines.append(line)

    # 添加关联关系（最多 20 条）
    if rels:
        table_lines.append("")
        table_lines.append("表之间的关联关系:")
        for r in rels[:20]:
            # ... 格式化关联关系 ...

    prompt = f"""表列表:
{chr(10).join(table_lines)}

用户问题: {question}

请选出相关的表。"""

    llm = get_llm(max_tokens=300, temperature=0.0)
    response = await llm.ainvoke([
        ("system", TABLE_SELECTION_SYSTEM),
        ("human", prompt),
    ])
    # ... 解析 LLM 返回的表名 ...
```

**Step 2: 选列** — `backend/app/ai/nodes/schema_selection.py:182-271`：

```python
async def _select_columns(question: str, selected_tables: list[str], metadata: dict) -> dict[str, list[str]]:
    # 构建选中表的字段列表（含类型、主键/外键标记、注释）
    col_lines = []
    for t in selected_tables:
        m = model_map.get(t)
        col_lines.append(f"表: {t} ({desc})")
        for c in m.get("columns", []):
            pk = " [主键]" if c.get("primary") else ""
            fk = " [外键]" if c.get("column_key") in ("FK", "MUL") else ""
            line = f"  - {cname} ({ctype}) {nullable}{pk}{fk}"
            col_lines.append(line)

    # ... LLM 调用 + 解析 "表名.字段名" 格式 ...
```

#### 逐行解读

1. **Step 1 只给表名+说明，不给字段** — 关键设计！字段信息太长（一张表可能 50+ 字段），
   全塞进去 LLM 会"注意力分散"，选表准确率反而下降。

2. **`rels[:20]`** — 限制关联关系最多 20 条，防止 prompt 过长。

3. **`TABLE_SELECTION_SYSTEM` 要求 LLM "每行输出一个表名"** — 这种"结构化输出"比让 LLM 输出 JSON 更可靠——
   JSON 格式容易出错（漏括号、多逗号），而逐行表名解析更鲁棒。

4. **`line.strip().lstrip("-0123456789.) *")`** — 去掉 LLM 可能添加的前缀符号。
   如 "- 1. orders" → "orders"，"* products" → "products"。

5. **Step 2 输出格式 "表名.字段名"** — 如 `orders.order_id`，后续用 `rsplit(".", 1)` 解析。
   用 `rsplit` 而非 `split`：防止表名中包含点号（如 schema.table.col）。

6. **兜底逻辑：LLM 没选出任何表时取前 5 张** — 宁可多给信息，也不能让后续节点无表可用。

7. **`schema_context += f"\n可用表名: {', '.join(all_table_names)}\n"`** — 在 schema_context 末尾列出所有合法表名，
   相当于给 SQL 生成节点的 LLM 一个"白名单"约束，防止幻觉出不存在的表名。

#### 两步 LLM 交互流程

```
用户问题: "上个月销售额最高的产品是什么"

Step 1: 选表
  输入: 问题 + 表列表（只有名字和说明）
    - t_orders (订单表)
    - t_products (产品表)
    - t_users (用户表)
    - t_categories (分类表)
    关联关系:
    - t_orders.product_id -> t_products.id
    - t_products.category_id -> t_categories.id
  输出: ["t_orders", "t_products"]

Step 2: 选列
  输入: 问题 + 选中表的字段列表
    表: t_orders (订单表)
      - id (INT) NOT NULL [主键]
      - product_id (INT) NOT NULL [外键]
      - amount (DECIMAL) NULL
      - order_time (DATETIME) NULL
    表: t_products (产品表)
      - id (INT) NOT NULL [主键]
      - name (VARCHAR) NULL
  输出: {"t_orders": ["id", "product_id", "amount", "order_time"],
         "t_products": ["id", "name"]}

最终 schema_context:
  可用的数据库表结构：
  表名: t_orders
  说明: 订单表
  字段:
    - id (INT) NOT NULL [主键]
    - product_id (INT) NOT NULL [外键]
    - amount (DECIMAL) NULL
    - order_time (DATETIME) NULL
  关联:
    - product_id -> t_products.id

  表名: t_products
  说明: 产品表
  字段:
    - id (INT) NOT NULL [主键]
    - name (VARCHAR) NULL

  可用表名: t_orders, t_products, t_users, t_categories
```

#### 动手练习

1. 在 `_select_tables` 函数中把 `max_tokens` 从 300 改为 50，观察 LLM 输出被截断后表名解析的效果。

2. 在 `schema_selection_node` 返回前打印 `schema_context`，观察传给 SQL 生成节点的完整上下文：

```python
logger.info("Schema context:\n%s", schema_context)
```

---

### 5.5 SQL 生成 — nodes/generation.py（三次降级 + 表名/列名校验修复）

#### 概念

SQL 生成是管线的核心环节，将用户的自然语言问题转化为可执行的 SQL。
ChatBI 采用**三次降级重试策略**，确保在各种情况下都能生成 SQL：

| 尝试 | Schema 来源 | 温度 | 对话历史 | 成功率 | 典型场景 |
|------|------------|------|---------|--------|---------|
| Attempt 1 | 精选 schema | 0.0 | 包含 | ~85% | 正常路径 |
| Attempt 2 | 完整 schema | 0.0 | 不包含 | ~10% | 精选 schema 遗漏了相关表 |
| Attempt 3 | 精选 schema | 0.7 | 不包含 | ~3% | 提示词约束太严格 |

生成后还有**幻觉修复**：校验表名和列名，用模糊匹配替换 LLM 编造的不存在的表名/列名。

#### 真实代码

**核心入口** — `backend/app/ai/nodes/generation.py:542-642`：

```python
async def generate_sql(
    question: str,
    schema_context: str,
    raw_metadata: str = "",
    history: list[dict] | None = None,
) -> dict:
    history_ctx = _build_history_context(history)
    attempt = 1

    # ---- Attempt 1: 精选 schema + 对话历史 ----
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", build_user_prompt(question, schema_context) + history_ctx),
    ]
    sql = await _llm_generate(messages, attempt="attempt1")

    # ---- Attempt 2: 完整 schema 重试 ----
    if sql is None and raw_metadata:
        attempt = 2
        full_schema = _build_full_schema_context(raw_metadata)
        retry_messages = [
            ("system", SYSTEM_PROMPT),
            ("human", build_user_prompt(question, full_schema)),
        ]
        sql = await _llm_generate(retry_messages, attempt="attempt2")

    # ---- Attempt 3: 高温度 LLM + 简化提示词 ----
    if sql is None:
        attempt = 3
        simple_prompt = (
            f"Based on the question: {question}\n\n"
            f"And this database schema:\n{schema_context}\n\n"
            f"Generate a valid MySQL SELECT query. "
            f"Only return the SQL statement, no explanation."
        )
        fb_llm = get_fallback_llm()  # temperature=0.7, max_tokens=4000
        sql = await _llm_generate(
            [("system", "You are a SQL expert. Generate accurate SELECT queries."),
             ("human", simple_prompt)],
            fb_llm,
            attempt="attempt3",
        )

    # 三次尝试全部失败
    if sql is None:
        return {"sql": "", "attempt": attempt, "table_fixes": [], "column_fixes": []}

    # 后处理：校验并修复表名和列名
    sql, table_fixes, column_fixes = _validate_and_fix_tables(sql, schema_context, raw_metadata)
    return {"sql": sql, "attempt": attempt, "table_fixes": table_fixes, "column_fixes": column_fixes}
```

**表名模糊匹配修复** — `backend/app/ai/nodes/generation.py:173-208`：

```python
def _fix_table_names(sql: str, invalid: set[str], valid: set[str]) -> str:
    for bad in invalid:
        best = max(valid, key=lambda t: SequenceMatcher(None, bad, t).ratio())
        if SequenceMatcher(None, bad, best).ratio() > _TABLE_NAME_SIM_THRESHOLD:
            logger.info("Replacing invalid table '%s' -> '%s'", bad, best)
            sql = re.sub(r'\b' + re.escape(bad) + r'\b', best, sql, flags=re.IGNORECASE)
    return sql
```

**列名幻觉修复** — `backend/app/ai/nodes/generation.py:238-325`：

```python
def _validate_and_fix_columns(sql: str, schema_context: str, raw_metadata: str = "") -> tuple[str, list[str]]:
    hallucinated_cols = {"created_at", "updated_at", "created_time", "update_time"}
    for hc in hallucinated_cols:
        if not re.search(r'\b' + hc + r'\b', sql, re.IGNORECASE):
            continue

        # 检查 SQL 引用的表中是否真的有这个列
        has_valid_col = False
        for t in sql_tables:
            cols = table_cols.get(t.lower(), set())
            if hc in cols:
                has_valid_col = True
                break

        if has_valid_col:
            continue  # 不是幻觉

        # 找最相似的时间类列替换
        for t in sql_tables:
            cols = table_cols.get(t.lower(), set())
            time_cols = [c for c in cols if any(
                kw in c.lower() for kw in ("time", "date", "at", "timestamp", "ts")
            )]
            if time_cols:
                best = max(time_cols, key=lambda c: SequenceMatcher(None, hc, c).ratio())
                ratio = SequenceMatcher(None, hc, best).ratio()
                replacement = best if ratio > 0.2 else time_cols[0]
                column_fixes.append(f"{hc} -> {replacement}")
                sql = re.sub(r'\b' + hc + r'\b', replacement, sql, flags=re.IGNORECASE)
                break

    return sql, column_fixes
```

#### 逐行解读

1. **`_build_history_context(history)`** — 构建对话历史上下文，只保留最近 6 条消息（约 3 轮对话），
   避免 prompt 过长。助手消息优先显示已执行的 SQL（比纯文本更有参考价值）。

2. **Attempt 1 → Attempt 2 的触发条件** — `if sql is None and raw_metadata`：
   LLM 返回空结果（`_llm_generate` 返回 None）且完整元数据可用时，用完整 schema 重试。

3. **Attempt 3 的极简提示词** — 去掉所有格式约束，只保留核心信息。
   system prompt 简化为 "You are a SQL expert"，temperature 提升到 0.7 增加创造性。

4. **`SequenceMatcher(None, bad, best).ratio()`** — Python 标准库 `difflib` 提供的序列相似度比较。
   返回 0.0~1.0 的分数，基于"最长公共子序列"算法。
   例如 `SequenceMatcher(None, "t_order", "t_orders").ratio()` ≈ 0.92。

5. **`_TABLE_NAME_SIM_THRESHOLD = 0.5`** — 表名替换的相似度阈值。
   只在相似度 > 0.5 时才替换，避免把完全无关的表名误替换。
   例如 `t_order` → `t_orders`（0.92，替换），`t_order` → `t_user`（0.33，不替换）。

6. **`hallucinated_cols = {"created_at", "updated_at", ...}`** — LLM 最容易"编造"的时间类列名。
   LLM 在训练数据中见过大量包含这些列的数据库表，即使目标表没有也会倾向于生成它们。

7. **列名替换阈值 0.2（比表名的 0.5 低很多）** — 因为列名差异通常更大（如 `created_at` vs `order_time`），
   如果阈值太高会漏掉很多可修复的情况。

#### 三次降级流程图

```
Attempt 1: 精选 schema + 对话历史 + temperature=0
    │
    ├── 成功 → 后处理（表名/列名校验修复）→ 返回
    │
    └── 失败（LLM 返回空）
         │
         ▼
Attempt 2: 完整 schema + 无对话历史 + temperature=0
         │
         ├── 成功 → 后处理 → 返回
         │
         └── 失败
              │
              ▼
Attempt 3: 精选 schema + 简化提示词 + temperature=0.7
              │
              ├── 成功 → 后处理 → 返回
              │
              └── 失败 → 返回 {"sql": "", ...}
```

#### 动手练习

1. 测试表名模糊匹配：

```python
from difflib import SequenceMatcher
print(SequenceMatcher(None, "t_order", "t_orders").ratio())   # ~0.92
print(SequenceMatcher(None, "t_order", "t_user").ratio())     # ~0.33
print(SequenceMatcher(None, "t_order", "t_order_detail").ratio())  # ~0.67
```

2. 在 `generate_sql` 的 Attempt 1 失败分支加日志，观察降级到 Attempt 2 的频率：

```python
logger.info("Attempt 1 failed, trying Attempt 2 with full schema")
```

---

### 5.6 SQL 执行 — nodes/execution.py（AST 安全校验 + 连接池 + 超时）

#### 概念

SQL 执行节点是管线的"动手"环节，把 LLM 生成的 SQL 真正送到数据库运行。
它有三道安全防线，确保即使 LLM 生成了危险 SQL 也不会造成破坏：

| 防线 | 机制 | 层级 | 拦截能力 |
|------|------|------|---------|
| 第 1 道 | SQLGlot AST 校验 | 应用层 | 拦截非 SELECT 语句（DROP/DELETE 等） |
| 第 2 道 | SET READ ONLY | 数据库层 | 即使绕过 AST，数据库也拒绝写操作 |
| 第 3 道 | asyncio.timeout | 应用层 | 防止慢查询卡死服务（默认 30 秒） |

#### 真实代码

**AST 安全校验** — `backend/app/ai/nodes/execution.py:51-99`：

```python
def validate_sql(sql: str, dialect: str = "mysql") -> tuple[bool, str]:
    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as e:
        return False, f"SQL 语法错误: {e}"

    if not isinstance(parsed, sqlglot.exp.Select):
        return False, "仅支持 SELECT 查询"

    # 第二层：正则扫描危险关键字
    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE"]
    pattern = re.compile(r'\b(' + '|'.join(dangerous_keywords) + r')\b', re.IGNORECASE)
    match = pattern.search(sql)
    if match:
        return False, f"禁止使用 {match.group(1)} 语句"

    return True, ""
```

**SQL 执行主函数** — `backend/app/ai/nodes/execution.py:102-236`：

```python
async def execute_sql(sql: str, datasource_id: str, dialect: str = "mysql", tenant_id: str | None = None) -> dict[str, Any]:
    # 第一道防线：AST 安全校验
    valid, error = validate_sql(sql, dialect)
    if not valid:
        return {"success": False, "error": error}

    try:
        # 获取数据库引擎（连接池）
        engine = await pool_manager.get_pool_by_id(datasource_id)

        start = time.monotonic()

        # 第二道 + 第三道防线：只读保护 + 超时控制
        async with asyncio.timeout(settings.sql_execution_timeout):
            async with engine.connect() as conn:
                try:
                    # 第二道防线：数据库层面只读保护
                    await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                except Exception:
                    await conn.execute(text("default_transaction_read_only = on"))

                # 执行 SQL
                result = await conn.execute(text(sql))
                columns = list(result.keys())
                rows = [dict(row._mapping) for row in result.fetchall()]

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 结果截断
        if len(rows) > settings.query_max_rows:
            rows = rows[:settings.query_max_rows]
            truncated = True

        # 类型序列化
        for row in rows:
            for k, v in row.items():
                if isinstance(v, (datetime.datetime, datetime.date)):
                    row[k] = str(v)
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")

        return {
            "success": True,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "execution_time_ms": elapsed_ms,
        }

    except asyncio.TimeoutError:
        return {"success": False, "error": f"查询超时（{settings.sql_execution_timeout}秒限制）"}
    except Exception as e:
        return {"success": False, "error": f"SQL 执行失败: {e}"}
```

#### 逐行解读

1. **`sqlglot.parse_one(sql, dialect=dialect)`** — 把 SQL 文本解析成 AST（抽象语法树）对象。
   如果 SQL 语法有误，会抛出 `ParseError`。AST 比正则匹配更可靠——能理解 SQL 结构，
   不会被注释、子查询绕过。

2. **`isinstance(parsed, sqlglot.exp.Select)`** — 核心安全检查：AST 的根节点必须是 Select 类型。
   如果用户写了 `DROP TABLE users`，`parse_one` 返回的是 `Drop` 类型，不是 `Select`。

3. **`\b` 词边界** — 正则中的 `\b` 防止误判。例如 `UPDATED_AT` 列名中的 `UPDATE` 不会触发拦截，
   因为 `UPDATE` 后面跟着 `D`，不是词边界。

4. **`SET SESSION TRANSACTION READ ONLY`** — MySQL 8.0+ 的只读事务语法。
   开启后，该连接上的所有写操作都会被数据库拒绝。这是比应用层校验更安全的保护——
   即使 SQL 绕过了 `validate_sql()`，数据库也会拦住。

5. **`except Exception: await conn.execute(text("default_transaction_read_only = on"))`** —
   兼容低版本 MySQL：5.7 及以下不支持 `SET SESSION TRANSACTION READ ONLY`，用降级方案。

6. **`asyncio.timeout(settings.sql_execution_timeout)`** — Python 3.11+ 的异步超时上下文管理器。
   超过 `settings.sql_execution_timeout`（默认 30 秒）自动抛出 `TimeoutError`，
   并取消正在执行的协程，防止资源泄漏。

7. **`rows[:settings.query_max_rows]`** — 结果截断，默认最多 1000 行。
   防止返回过多数据导致内存溢出或前端卡顿。

8. **`isinstance(v, (datetime.datetime, datetime.date))`** — 类型序列化：
   JSON 标准只支持 string/number/boolean/null/array/object，
   Python 的 `datetime` 和 `bytes` 无法直接序列化，必须先转换。

#### 三道防线示意图

```
LLM 生成的 SQL
    │
    ▼
┌─────────────────────────────────┐
│ 第 1 道：AST 校验               │
│ sqlglot.parse_one() → Select?  │
│ + 正则扫描 DROP/DELETE 等       │
└────────┬────────────────────────┘
         │ 通过
         ▼
┌─────────────────────────────────┐
│ 第 2 道：数据库只读保护          │
│ SET SESSION TRANSACTION READ ONLY│
│ 数据库层面拒绝写操作             │
└────────┬────────────────────────┘
         │ 通过
         ▼
┌─────────────────────────────────┐
│ 第 3 道：超时控制                │
│ asyncio.timeout(30s)            │
│ 超时自动取消协程                 │
└────────┬────────────────────────┘
         │ 通过
         ▼
    执行 SQL → 返回结果
```

#### 动手练习

1. 测试 AST 校验拦截危险 SQL：

```python
from app.ai.nodes.execution import validate_sql
print(validate_sql("SELECT * FROM users"))           # (True, "")
print(validate_sql("DROP TABLE users"))              # (False, "仅支持 SELECT 查询")
print(validate_sql("SELECT * FROM (DELETE FROM users) AS t"))  # (False, "禁止使用 DELETE 语句")
```

2. 修改 `settings.sql_execution_timeout` 为 5 秒，执行一个慢查询，观察超时保护的效果。

---

### 5.7 自愈修复 — nodes/self_heal.py（执行失败后迭代修复）

#### 概念

当 SQL 执行失败时，自愈节点会把错误信息 + 原 SQL + schema 交给 LLM，让它尝试修复 SQL。
这是"错误 → 诊断 → 修复 → 再执行"的闭环，最多重试 2 次。

#### 真实代码

**自愈主循环** — `backend/app/ai/nodes/self_heal.py:171-266`：

```python
async def self_heal_sql(
    question: str,
    sql: str,
    error: str,
    datasource_id: str = "",
    schema_context: str = "",
    dialect: str = "mysql",
    retry_count: int = 1,
) -> dict[str, Any]:
    from app.ai.nodes.execution import execute_sql

    while retry_count <= settings.llm_self_heal_max_retries:
        prompt = build_fix_prompt(question, sql, error, retry_count, schema_context)

        try:
            llm = get_llm()
            async with asyncio.timeout(30):
                response = await llm.ainvoke([
                    ("system", "你只生成 SQL，不解释。"),
                    ("human", prompt),
                ])
            fixed_sql = _strip_markdown(response.content)

            if not fixed_sql:
                retry_count += 1
                continue

            result = await execute_sql(fixed_sql, datasource_id, dialect)

            if result["success"]:
                logger.info("SQL self-heal succeeded on retry %d: %s", retry_count, fixed_sql[:200])
                return {"success": True, "sql": fixed_sql, "fixed": True, **result}

            error = result.get("error", "")
            retry_count += 1

        except Exception as e:
            logger.error("Self-healing LLM call failed: %s", e)
            retry_count += 1

    return {"success": False, "error": f"SQL 修复失败（已重试 {settings.llm_self_heal_max_retries} 次）"}
```

**修复 prompt 构建** — `backend/app/ai/nodes/self_heal.py:100-142`：

```python
def build_fix_prompt(question: str, failed_sql: str, error: str, retry_count: int, schema_context: str) -> str:
    error_code = extract_error_code(error)
    error_desc = AUTO_FIX_RULES.get(error_code, error[:200])

    return f"""你是 SQL 修复专家。请修复以下 SQL 的错误。

原始问题：{question}
失败的 SQL：{failed_sql}
错误信息：{error}
错误类型：{error_desc}

{f"这是第 {retry_count} 次重试，请仔细检查。" if retry_count > 1 else ""}

{f"可用的表结构：\n{schema_context}" if schema_context else ""}

请只返回修复后的 SQL，不要包含任何解释或 markdown 代码块。"""
```

**错误码提取** — `backend/app/ai/nodes/self_heal.py:73-97`：

```python
AUTO_FIX_RULES = {
    "1146": "表不存在",
    "1054": "列不存在",
    "1064": "SQL 语法错误",
    "1049": "数据库不存在",
}

def extract_error_code(error_msg: str) -> str:
    m = _ERRNO_RE.search(error_msg)   # 匹配 "(errno: 1054)"
    if m:
        return m.group(1)
    m = _GENERIC_RE.search(error_msg)  # 匹配 "(1054)"
    if m:
        return m.group(1)
    return ""
```

#### 逐行解读

1. **`while retry_count <= settings.llm_self_heal_max_retries:`** — 自愈主循环。
   `llm_self_heal_max_retries` 默认为 2，即最多重试 2 次。
   设计权衡：重试太少可能错过可修复的错误，太多则浪费 LLM 调用并增加延迟。

2. **`build_fix_prompt(...)`** — 将错误上下文格式化为 LLM 可理解的修复指令。
   包含：原始问题、失败的 SQL、错误信息、错误类型（中文描述）、重试次数、表结构。

3. **`AUTO_FIX_RULES`** — MySQL 错误码到中文描述的映射。
   1146=表不存在、1054=列不存在、1064=语法错误、1049=数据库不存在。
   将错误码映射为中文描述后，prompt 中会告诉 LLM "错误类型：列不存在"，
   而非原始的英文错误信息，帮助 LLM 更精准地修正。

4. **`error = result.get("error", "")`** — 修复后仍然失败时，用新的错误信息更新 `error` 变量。
   下一轮重试时 LLM 能看到上一轮的修复结果和新错误，形成"错误 → 诊断 → 修复"闭环。

5. **`{**result}`** — 字典解包，将 `execute_sql` 返回的所有字段（如 data, columns）合并到返回结果中。

6. **延迟导入 `from app.ai.nodes.execution import execute_sql`** — 放在函数内部避免循环依赖。
   `self_heal.py` 调用 `execution.py`，而 `execution.py` 可能间接引用 `self_heal.py`。

#### 自愈循环流程

```
SQL 执行失败
    │
    ▼
┌─────────────────────────────────────┐
│ Retry 1:                            │
│   构建 prompt（错误信息 + schema）   │
│   → LLM 生成修复 SQL                │
│   → execute_sql(修复后 SQL)          │
│   ├── 成功 → 返回结果               │
│   └── 失败 → 用新错误更新 error     │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│ Retry 2:                            │
│   构建 prompt（新错误 + schema）     │
│   → LLM 生成修复 SQL                │
│   → execute_sql(修复后 SQL)          │
│   ├── 成功 → 返回结果               │
│   └── 失败 → 放弃修复               │
└─────────────────────────────────────┘
```

#### 动手练习

1. 测试错误码提取：

```python
from app.ai.nodes.self_heal import extract_error_code, AUTO_FIX_RULES
code = extract_error_code("(errno: 1054) Unknown column 'created_at'")
print(code)  # "1054"
print(AUTO_FIX_RULES.get(code))  # "列不存在"
```

2. 在 `self_heal_sql` 的 while 循环入口加日志，观察自愈被触发的频率：

```python
logger.info("Self-heal attempt %d for SQL: %s", retry_count, sql[:100])
```

---

### 5.8 图表推断 — chart_type.py（纯规则，无 LLM）

#### 概念

根据 SQL 查询结果的列数、行数、数据类型等特征，自动推断最适合的图表类型。
**不依赖 LLM，纯规则匹配，毫秒级完成。**

为什么用规则而不用 LLM？
1. 速度：规则推断 <1ms，LLM 调用 500-2000ms
2. 确定性：相同输入永远返回相同结果
3. 成本：每次查询省一次 LLM 调用
4. 可解释性：规则透明可调试

#### 真实代码

**主入口函数** — `backend/app/ai/chart_type.py:46-114`：

```python
def infer_chart_type(columns: list[str], rows: list[dict]) -> ChartType:
    col_count = len(columns)
    row_count = len(rows)

    if col_count == 0 or row_count == 0:
        return "table"

    # 单值指标：SELECT COUNT(*) FROM orders
    if col_count == 1 and row_count == 1:
        return "metric"

    if col_count == 2:
        first_col = _sample_value(rows, columns[0])
        second_col = _sample_value(rows, columns[1])

        # 时间序列 → 折线图
        if _looks_like_time(first_col) or _looks_like_time(second_col):
            return "line"

        # 分类少 + 数值列 → 饼图
        if row_count <= 5 and _is_numeric(second_col):
            return "pie"

        # 分类 + 数值 → 柱状图
        if _is_numeric(second_col):
            return "bar"

    if col_count == 3:
        third_col = _sample_value(rows, columns[2])
        if _is_numeric(third_col):
            return "grouped_bar"

    if col_count >= 2 and row_count >= 10:
        num_cols = [c for c in columns if _is_numeric_column(c, rows)]
        if len(num_cols) >= 2:
            return "scatter"

    return "table"
```

**时间列检测** — `backend/app/ai/chart_type.py:163-185`：

```python
def _looks_like_time(value: Any) -> bool:
    if value is None:
        return False
    s = str(value).lower()
    time_indicators = ["年", "月", "日", "quarter", "week", "date", "time",
                       "-01", "-02", "-03", "-04", "-05", "-06",
                       "-07", "-08", "-09", "-10", "-11", "-12", "20"]
    return any(ind in s for ind in time_indicators)
```

#### 逐行解读

1. **`col_count == 1 and row_count == 1` → `"metric"`** — 单值指标。
   如 `SELECT COUNT(*) FROM orders` 只有一个数字，适合指标卡展示。

2. **`_looks_like_time(first_col)`** — 启发式判断值是否为时间/日期类型。
   通过字符串包含的关键词判断（中英文时间关键词 + 月份前缀 + 世纪前缀 "20"），
   而非正则或 datetime 解析。原因：SQL 查询结果的时间列格式不统一，关键词匹配覆盖面更广。

3. **`row_count <= 5` → `"pie"`** — 饼图行数阈值 5 是经验值：超过 5 个扇区可读性急剧下降。

4. **`_sample_value(rows, columns[0])`** — 从第一行提取样本值，用于快速判断列的数据类型。
   权衡：样本可能不具代表性，但性能远优于全量扫描。

5. **`_is_numeric_column(c, rows)`** — 判断整列是否为数值列（采样前 3 行，超过 50% 为数值即判定）。

#### 推断规则总览

```
列数 × 行数 × 数据类型 → 图表类型
┌──────────┬──────────────┬──────────────────────────┐
│ 列数     │ 条件         │ 图表类型                 │
├──────────┼──────────────┼──────────────────────────┤
│ 1列1行   │ -            │ metric (指标卡)          │
│ 2列      │ 含时间列     │ line (折线图)            │
│ 2列      │ ≤5行+数值列  │ pie (饼图)               │
│ 2列      │ 数值列       │ bar (柱状图)             │
│ 3列      │ 第3列是数值  │ grouped_bar (分组柱状图) │
│ ≥2列     │ ≥10行+2数值列│ scatter (散点图)         │
│ 其他     │ -            │ table (表格)             │
└──────────┴──────────────┴──────────────────────────┘
```

#### 动手练习

1. 测试各种数据特征的图表推断：

```python
from app.ai.chart_type import infer_chart_type

# 指标卡
print(infer_chart_type(["销售额"], [{"销售额": 15000}]))  # "metric"

# 折线图
print(infer_chart_type(["月份", "销售额"], [{"月份": "2024-01", "销售额": 15000}]))  # "line"

# 柱状图
print(infer_chart_type(["城市", "销售额"], [{"城市": "北京", "销售额": 15000}]))  # "bar"

# 饼图
print(infer_chart_type(["状态", "数量"], [{"状态": "已付款", "数量": 100}, {"状态": "待付款", "数量": 50}]))  # "pie"

# 表格（兜底）
print(infer_chart_type(["a", "b", "c", "d"], [{"a": 1, "b": 2, "c": 3, "d": 4}]))  # "table"
```

---

### 5.9 Prompt 工程 — prompts/query_prompt.py（系统提示词设计）

#### 概念

Prompt 工程是 NL→SQL 管线中影响 SQL 生成质量的最关键因素。
`query_prompt.py` 定义了 LLM 的系统提示词（SYSTEM_PROMPT）和用户提示词构建函数，
与 `generation.py` 的逻辑代码分离，方便独立修改和迭代。

#### 真实代码

**系统提示词** — `backend/app/ai/prompts/query_prompt.py:39-62`：

```python
SYSTEM_PROMPT = """你是一个专业的 SQL 生成助手。你的任务根据用户的自然语言问题和提供的数据库结构，生成准确的 SQL 查询。

## 规则
1. 只生成 SELECT 语句，禁止任何修改操作（INSERT/UPDATE/DELETE/CREATE/ALTER/DROP）
2. 使用标准 SQL 语法，兼容 MySQL
3. 表名必须严格使用"可用表名"列表中提供的名称，不得增减或修改（如禁止将 t_orders 写为 orders）
4. 列名必须严格使用 schema 中提供的名称，不得臆造不存在的列
5. 如果问题涉及多个表，使用正确的 JOIN 关系
6. 对于聚合查询，使用 GROUP BY + HAVING
7. 对于排序查询，使用 ORDER BY，默认降序
8. 对于 Top N 查询，使用 LIMIT，默认 100 条
9. 每个查询字段必须使用中文别名（AS '中文名'），别名应简洁易懂，优先使用字段注释中的中文名。聚合字段也要有中文别名，如 COUNT(*) AS '数量', AVG(price) AS '平均价格'
10. 禁止使用 CREATE TEMPORARY TABLE、子查询中的 DDL 等结构
11. 只返回 SQL 语句本身，不要解释、不要 markdown 代码块
12. 如果问题无法直接映射到表结构，尝试使用最相关的表和通用聚合函数，不要返回空

## 时间字段规则（最重要！）
- 当用户提到"最近N天/本周/本月"等时间条件时，必须先在 schema 中找到实际的时间字段名
- 常见时间字段名：created_at, updated_at, timestamp, recorded_at, start_time, date 等
- 绝对禁止假设存在 created_at 字段！必须使用 schema 中实际列出的时间字段
- 如果 schema 中没有时间字段，则不要添加时间过滤条件

## 输出格式
仅输出一条 SQL 语句，以分号结尾。"""
```

**基础用户提示词** — `backend/app/ai/prompts/query_prompt.py:82-88`：

```python
def build_user_prompt(question: str, schema_context: str) -> str:
    return f"""数据库结构：
{schema_context}

问题：{question}

请生成对应的 SQL 查询语句。"""
```

**增强版用户提示词** — `backend/app/ai/prompts/query_prompt.py:122-172`：

```python
def build_semantic_prompt(question: str, schema_context: str, semantics: dict) -> str:
    """构建包含语义分析结果的 prompt。"""
    parts = [f"数据库结构：\n{schema_context}", f"\n问题：{question}"]

    if semantics:
        parts.append("\n## 语义分析结果")
        intent = semantics.get("intent") or "DataQuery"
        parts.append(f"- 查询类型: {intent}")

        metric = semantics.get("metric")
        if metric:
            func = metric.get("function", "SELECT")
            col = metric.get("column", "?")
            parts.append(f"- 指标: {func}({col})")

        dims = semantics.get("dimensions")
        if dims:
            cols = ", ".join(d["column"] for d in dims)
            parts.append(f"- 分组维度: {cols}")

        filters = semantics.get("filters")
        if filters:
            conditions = []
            for f in filters:
                col = f.get("column", "?")
                op = f.get("operator", "=")
                val = str(f.get("value", "?")).replace("'", "''")
                conditions.append(f"{col} {op} '{val}'")
            parts.append(f"- 过滤条件: {' AND '.join(conditions)}")

        # ... 时间范围、排序、限制 ...

    parts.append("\n请根据以上语义分析结果生成对应的 SQL 查询语句。")
    return "\n".join(parts)
```

#### 逐行解读

1. **规则 3/4：强制使用 schema 中的表名/列名** — 防止 LLM "幻觉"出不存在的表名或列名。
   例如禁止把 `t_orders` 写成 `orders`，禁止编造 `created_at` 列。

2. **规则 9：中文别名** — `COUNT(*) AS '数量'`，让前端表格直接展示可读列名，无需二次映射。
   这是 ChatBI 的特色设计，因为目标用户是中文用户。

3. **规则 11：只输出 SQL** — 禁止解释和 markdown，简化下游解析。
   `_clean_sql()` 函数会做兜底处理（去 markdown 标记、提取 SELECT），但 prompt 中先约束能减少后处理负担。

4. **"时间字段规则"单独成段** — 因为这是 LLM 最容易犯的错误。
   LLM 在训练数据中见过大量包含 `created_at` 列的数据库表，即使目标表没有也会倾向于生成它。
   单独强调比混在通用规则中更有效。

5. **`build_user_prompt(question, schema_context)`** — 基础版：直接拼接 schema + question。
   简单但有效，是 Attempt 1 和 Attempt 2 使用的版本。

6. **`build_semantic_prompt(question, schema_context, semantics)`** — 增强版：额外注入语义分析结果
   （intent / metric / dimensions / filters / time_range / sort / limit）。
   当前已在 `generation.py` 中导入，但尚未在主流程中启用。未来语义分析路径打通后，
   将替代 `build_user_prompt` 作为主要提示词。

7. **`val.replace("'", "''")`** — 单引号转义，防止 SQL 注入风险。
   如 `O'Brien` → `O''Brien`，这是 SQL 标准的转义方式。

8. **提示词与代码分离** — 修改提示词不需要动 `generation.py` 的逻辑，反之亦然。
   这是 Prompt 工程的最佳实践：提示词是"配置"，代码是"引擎"。

#### SYSTEM_PROMPT 结构拆解

```
SYSTEM_PROMPT
├── 角色定义："你是一个专业的 SQL 生成助手"
├── 通用规则（12 条）
│   ├── 安全规则：1（只 SELECT）、10（禁止 DDL）
│   ├── 命名规则：3（表名白名单）、4（列名白名单）
│   ├── SQL 规则：5（JOIN）、6（GROUP BY）、7（ORDER BY）、8（LIMIT）
│   ├── 输出规则：9（中文别名）、11（只输出 SQL）
│   └── 兜底规则：12（无法映射时用最相关表）
├── 时间字段规则（重点强调）
│   ├── 必须用 schema 中的实际字段
│   ├── 禁止假设 created_at
│   └── 无时间字段时不加过滤
└── 输出格式：一条 SQL + 分号结尾
```

#### 动手练习

1. 修改 `SYSTEM_PROMPT` 中的规则 8，把默认 LIMIT 从 100 改为 50，观察生成的 SQL 变化。

2. 在 `build_user_prompt` 中添加调试输出，观察传给 LLM 的完整 prompt：

```python
def build_user_prompt(question: str, schema_context: str) -> str:
    prompt = f"""数据库结构：
{schema_context}

问题：{question}

请生成对应的 SQL 查询语句。"""
    logger.info("User prompt length: %d chars", len(prompt))
    return prompt
```

3. 尝试启用 `build_semantic_prompt`：在 `generation.py` 的 Attempt 1 中把 `build_user_prompt` 替换为 `build_semantic_prompt`，
   传入模拟的 semantics 字典，观察 SQL 生成质量的变化。


## 第 6 章：管线执行器 — SSE 流式 + 异步查询

本章解读 ChatBI 最核心的调度模块 `pipeline_executor.py`，以及它与前端 SSE 长连接的协作方式。
理解本章后，你将掌握：async generator 如何实现"边算边推"、缓存命中/未命中的两条路径、
7 种 SSE 事件的结构和含义、前后端如何通过 StreamingResponse 对接。

---

### 6.1 async generator — pipeline_executor.py 的 async def execute_query_pipeline() + yield

#### 概念

Python 的 **async generator**（异步生成器）是 `async def` + `yield` 的组合。
普通函数 `return` 一次就结束，而生成器可以 `yield` 多次——每次 yield 产出一个值，
调用方用 `async for` 逐个消费。在 ChatBI 中，这意味着前端不需要等整个 AI 管线跑完，
每完成一步（意图识别、Schema 选择、SQL 生成...）就能立刻看到进度。

#### 真实代码

`backend/app/services/pipeline_executor.py:8` — 函数签名：

```python
async def execute_query_pipeline(question: str, datasource_id: str, tenant_id: str, history: list[dict] | None = None):
```

`backend/app/services/pipeline_executor.py:30` — 第一个 yield（缓存命中事件）：

```python
yield {"event": "cache", "data": {"hit": True, "type": "exact", "duration_ms": cache_duration}}
```

`backend/app/services/pipeline_executor.py:37` — 第二个 yield（意图识别事件）：

```python
yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图（关键词匹配）", "duration_ms": intent_duration, "method": "cached"}}
```

#### 逐行解读

1. **`async def`** — 声明这是一个异步函数。内部可以用 `await` 等待其他异步操作（如 LLM 调用、数据库查询）。
2. **`yield`** — 产出一个值给调用方，但函数不会结束，而是"暂停"在 yield 处，等调用方再次请求时继续执行。
   这与 `return` 的关键区别：return 结束函数，yield 暂停函数。
3. **`{"event": "cache", "data": {...}}`** — 每次产出的事件都是一个字典，包含 `event`（事件类型）和 `data`（事件数据）。
   这是整个管线执行器与外界的"通信协议"。
4. **`history: list[dict] | None = None`** — 对话历史参数，用于上下文补全。`| None` 是 Python 3.10+ 的联合类型写法。
5. **函数内部没有 `return` 值** — 生成器函数的传统 return 值是 None，但调用方通过 `async for` 收集所有 yield 产出的事件。

#### 调用方式

在 `backend/app/api/query.py:727`，SSE 路由这样消费生成器：

```python
async for event in execute_query_pipeline(data.question, data.datasource_id, tenant_id, data.history):
    yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
```

每一轮 `async for` 循环：
1. `execute_query_pipeline` 执行到下一个 `yield`，产出事件字典
2. API 层把事件字典格式化为 SSE 文本行（`event: xxx\ndata: {...}\n\n`）
3. FastAPI 的 `StreamingResponse` 把文本行推送到前端

#### 动手练习

1. 在 Python REPL 中写一个简单的 async generator：

```python
import asyncio

async def simple_pipeline():
    yield {"event": "step1", "data": {"msg": "开始"}}
    await asyncio.sleep(1)  # 模拟异步操作
    yield {"event": "step2", "data": {"msg": "完成"}}

async def main():
    async for event in simple_pipeline():
        print(event)

asyncio.run(main())
```

2. 在 `pipeline_executor.py` 的 `execute_query_pipeline` 函数入口处加一行日志，观察事件产出顺序：

```python
logger.info("Pipeline started for question: %s", question[:50])
```

---

### 6.2 cache-hit 路径 — 精确缓存 vs 语义缓存

#### 概念

管线执行器在启动时首先检查缓存，按优先级分两层：

| 缓存类型 | 匹配方式 | 命中后行为 | 典型场景 |
|---------|---------|-----------|---------|
| 精确缓存 | 问题文本完全一致（SHA256 哈希） | 复用 SQL，跳过 AI 生成 | 用户重复点击"执行" |
| 语义缓存 | 问题语义相似（词重叠度 >= 0.8） | 复用 SQL，跳过 AI 生成 | "各城市销量" vs "按城市统计销量" |

**关键设计**：缓存命中后，仍需执行 SQL（数据可能已变化），但省掉了最耗时的 AI 生成步骤（Schema 选择 + SQL 生成，通常 3-8 秒）。

#### 真实代码

**精确缓存命中** — `backend/app/services/pipeline_executor.py:25-30`：

```python
cached = await cache_get(question, datasource_id, tenant_id)
if cached:
    cache_duration = int((time.monotonic() - step_start) * 1000)
    yield {"event": "cache", "data": {"hit": True, "type": "exact", "duration_ms": cache_duration}}
```

**语义缓存命中** — `backend/app/services/pipeline_executor.py:136-140`：

```python
sem_cached = await semantic_cache_get(question, datasource_id, tenant_id)
if sem_cached:
    cache_duration = int((time.monotonic() - step_start) * 1000)
    yield {"event": "cache", "data": {"hit": True, "type": "semantic", "duration_ms": cache_duration}}
```

**缓存命中后的 SQL 安全校验** — `backend/app/services/pipeline_executor.py:68-76`：

```python
import sqlglot
try:
    parsed = sqlglot.parse_one(cached_sql, dialect="mysql")
    stmt_type = parsed.key.upper() if parsed.key else "UNKNOWN"
    if stmt_type != "SELECT":
        yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"缓存 SQL 非 SELECT 语句 ({stmt_type})，拒绝执行", ...}}
        final_error = f"缓存 SQL 安全校验失败: 仅允许 SELECT 语句，检测到 {stmt_type}"
        yield {"event": "complete", "data": {"success": False, "error": final_error}}
        return
```

#### 逐行解读

1. **`await cache_get(question, datasource_id, tenant_id)`** — 先查精确缓存。`cache_get` 内部用 SHA256 哈希生成 key，查 Redis。
2. **`if cached:`** — 命中则进入精确缓存路径，跳过 AI 生成。
3. **`yield {"event": "cache", ...}`** — 立即通知前端缓存命中，前端可以显示"精确缓存命中，Xms"。
4. **`await semantic_cache_get(...)`** — 精确缓存未命中，再查语义缓存。语义缓存用词重叠度（Jaccard 系数）判断相似性。
5. **`sqlglot.parse_one(cached_sql, dialect="mysql")`** — 即使缓存命中，也要用 SQLGlot 做 AST 校验。
   为什么？因为缓存的 SQL 可能因表结构变更而变成危险语句（如被篡改为 DELETE）。
   这是"不信任缓存"的安全原则。
6. **`if stmt_type != "SELECT":`** — 安全红线：只允许 SELECT 语句。非 SELECT 直接拒绝执行。

#### 缓存命中后的流程

```
缓存命中 → yield cache 事件 → 意图识别 → yield intent 事件
         → 从缓存 SQL 反推 Schema → yield semantics 事件
         → SQL 安全校验（AST） → yield sql 事件
         → 执行 SQL → yield data 事件
         → 失败则自愈 → yield sql + data 事件
         → 图表推断 → yield chart 事件
         → yield complete 事件
```

注意：缓存命中后仍然走意图识别，因为用户可能这次问的是闲聊（虽然文本相同，但意图可能因上下文变化）。

#### 动手练习

1. 在 Redis CLI 中查看缓存 key：

```bash
redis-cli keys "query:*"   # 精确缓存
redis-cli keys "semantic:*"  # 语义缓存索引
```

2. 故意让缓存 SQL 变成非法语句，观察 AST 校验如何拦截：

```bash
redis-cli set "query:test" '{"sql":"DROP TABLE users","success":true,"rows":[]}'
```

---

### 6.3 cache-miss 路径 — 完整管线执行

#### 概念

当精确缓存和语义缓存都未命中时，管线执行器走完整路径，依次调用 6 个 AI 节点：

```
意图识别 → Schema 选择 → SQL 生成 → SQL 执行 → 自愈(失败时) → 图表推断
```

这是最慢的路径（通常 5-15 秒），但也是最有价值的路径——它真正体现了 AI 的能力。

#### 真实代码

**缓存未命中事件** — `backend/app/services/pipeline_executor.py:243`：

```python
yield {"event": "cache", "data": {"hit": False, "duration_ms": cache_duration}}
```

**Step 1: 意图识别** — `backend/app/services/pipeline_executor.py:247-252`：

```python
from app.ai.nodes.intent import classify_intent
intent = await classify_intent(question)
intent_duration = int((time.monotonic() - step_start) * 1000)
intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图", "duration_ms": intent_duration}}
```

**Step 2: Schema 选择** — `backend/app/services/pipeline_executor.py:260-276`：

```python
from app.ai.nodes.schema_selection import schema_selection_node
state = {
    "question": question,
    "datasource_id": datasource_id,
    "tenant_id": tenant_id,
}
schema_result = await schema_selection_node(state)
schema_context = schema_result.get("schema_context", "")
raw_metadata = schema_result.get("raw_metadata", "")
selected_tables = schema_result.get("selected_tables", [])
selected_columns = schema_result.get("selected_columns", {})
```

**Step 3: SQL 生成** — `backend/app/services/pipeline_executor.py:278-302`：

```python
from app.ai.nodes.generation import generate_sql
gen_result = await generate_sql(question, schema_context, raw_metadata=raw_metadata, history=history)
```

**Step 4: SQL 执行** — `backend/app/services/pipeline_executor.py:308-321`：

```python
from app.ai.nodes.execution import execute_sql
exec_result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)
```

**Step 5: 自愈（失败时）** — `backend/app/services/pipeline_executor.py:323-342`：

```python
if not final_success and schema_context:
    from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
    error_code = extract_error_code(final_error or "")
    heal_result = await self_heal_sql(
        question=question, sql=sql, error=final_error or "",
        datasource_id=datasource_id, schema_context=schema_context, dialect="mysql",
    )
```

**Step 6: 图表推断** — `backend/app/services/pipeline_executor.py:346-349`：

```python
if exec_result.get("rows") and exec_result.get("columns"):
    chart_type = infer_chart_type(exec_result["columns"], exec_result["rows"])
```

#### 逐行解读

1. **延迟导入（lazy import）** — 所有 `from app.ai.nodes.xxx import` 都在函数内部而非文件顶部。
   原因：避免循环依赖（pipeline_executor 被 query.py 导入，而节点模块可能间接引用 query.py 的 schema），
   且减少启动时间（不用的节点不会被加载）。
2. **`time.monotonic()`** — 单调递增时钟，不受系统时间调整影响。比 `time.time()` 更适合计算耗时。
3. **`schema_result.get("schema_context", "")`** — 用 `.get()` 而非 `[]` 取值，因为 schema_selection 可能返回空结果。
4. **`if not final_success and schema_context:`** — 自愈的两个前提：执行失败 + 有 schema 上下文。
   没有 schema 的话，LLM 无法知道正确的表名/列名，自愈没有意义。
5. **`infer_chart_type(columns, rows)`** — 纯规则推断，不调用 LLM，毫秒级完成。
6. **`is_slow = total_ms > settings.slow_query_threshold_ms`** — 慢查询标记，前端据此显示警告。

#### 完整管线流程图

```
                    ┌─────────────────────┐
                    │   缓存未命中         │
                    │   yield cache(miss) │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Step 1: 意图识别    │
                    │  classify_intent()  │
                    │  yield intent       │
                    └──────────┬──────────┘
                               │
                     ┌─────────▼─────────┐
                     │  DataQuery?       │
                     └──┬─────────────┬──┘
                        │ Yes         │ No
               ┌────────▼────┐  ┌────▼────────┐
               │ Step 2:      │  │ yield       │
               │ Schema 选择  │  │ complete    │
               │ yield sem.   │  │ (error)     │
               └────────┬────┘  └─────────────┘
                        │
               ┌────────▼────┐
               │ Step 3:      │
               │ SQL 生成     │
               │ yield sql    │
               └────────┬────┘
                        │
               ┌────────▼────┐
               │ Step 4:      │
               │ SQL 执行     │
               │ yield data   │
               └────────┬────┘
                        │
                  ┌─────▼──────┐
                  │ 成功?       │
                  └──┬──────┬──┘
                     │ Yes  │ No
          ┌──────────▼──┐   │
          │ Step 6:      │   │
          │ 图表推断     │   ┌▼───────────┐
          │ yield chart  │   │ Step 5:     │
          └──────┬───────┘   │ SQL 自愈    │
                 │           │ yield sql   │
                 │           │ + data      │
                 │           └──────┬──────┘
                 │                  │
                 └──────┬───────────┘
                        │
               ┌────────▼────┐
               │ yield       │
               │ complete    │
               └─────────────┘
```

#### 动手练习

1. 在后端日志中追踪完整管线执行：

```bash
tail -f backend/logs/app.log | grep "Pipeline\|Intent\|Schema\|SQL\|execute"
```

2. 故意问一个非数据查询问题（如"你好"），观察意图识别如何短路流程。

---

### 6.4 事件格式 — {event, data} 结构，7 种事件类型

#### 概念

管线执行器产出的每个事件都是一个字典 `{"event": "事件类型", "data": {...}}`。
共 8 种事件类型（7 种正常 + 1 种异常兜底），按固定顺序产出：

| 序号 | 事件名 | 含义 | data 关键字段 | 产出时机 |
|-----|--------|------|-------------|---------|
| 0 | `cache` | 缓存命中/未命中 | `hit`, `type`, `duration_ms` | 管线启动时 |
| 1 | `intent` | 意图识别结果 | `intent`, `detail`, `duration_ms` | 意图识别完成后 |
| 2 | `semantics` | Schema 选择结果 | `detail`, `tables`, `columns`, `duration_ms` | Schema 选择完成后 |
| 3 | `sql` | 生成的 SQL | `sql`, `detail`, `duration_ms`, `validation` | SQL 生成/自愈后 |
| 4 | `data` | 查询执行结果 | `success`, `columns`, `rows`, `row_count`, `duration_ms` | SQL 执行完成后 |
| 5 | `chart` | 推荐图表类型 | `chart_type`, `detail` | 图表推断完成后 |
| 6 | `complete` | 管线结束 | `success`, `is_slow`, `cached` | 所有步骤完成后 |
| 7 | `error` | 未预期异常 | `error` | 管线异常时（兜底） |

#### 真实代码

各事件的 yield 位置（`backend/app/services/pipeline_executor.py`）：

```python
# cache 事件 — 第 30 行（精确缓存命中）
yield {"event": "cache", "data": {"hit": True, "type": "exact", "duration_ms": cache_duration}}

# intent 事件 — 第 37 行
yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图（关键词匹配）", "duration_ms": intent_duration, "method": "cached"}}

# semantics 事件 — 第 63 行
yield {"event": "semantics", "data": {"detail": table_detail, "tables": selected_tables, "columns": selected_columns, "duration_ms": schema_duration, "source": "cached"}}

# sql 事件 — 第 82 行
yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"使用缓存 SQL（精确缓存命中，{cache_duration}ms）", "duration_ms": sql_duration, "source": "cached"}}

# data 事件 — 第 96 行
yield {"event": "data", "data": {**exec_result, "detail": exec_detail, "duration_ms": exec_duration}}

# chart 事件 — 第 129 行
yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

# complete 事件 — 第 133 行
yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": True, "cache_type": "exact"}}

# error 事件 — 第 360 行
yield {"event": "error", "data": {"error": str(e)}}
```

#### 逐行解读

1. **`"event": "cache"`** — 事件类型标识，前端据此决定如何渲染。前端 `chatStore.ts` 的 `handleSSEEvent` 函数用 `switch(eventType)` 分发处理。
2. **`"data": {"hit": True, "type": "exact", ...}`** — 事件数据，每种事件有不同的字段。
   `hit` 表示是否命中，`type` 区分精确/语义缓存。
3. **`{**exec_result, "detail": exec_detail, ...}`** — 字典解包。`exec_result` 包含 `success`, `columns`, `rows` 等字段，
   解包后再添加 `detail` 和 `duration_ms`，形成完整的 data 事件数据。
4. **`"source": "cached"`** — 标记数据来源，前端据此显示"使用缓存 SQL"而非"AI 生成 SQL"。
5. **`"is_slow": is_slow`** — complete 事件中的慢查询标记，前端据此显示红色警告。
6. **`"validation": {"table_fixes": [...], "column_fixes": [...]}`** — sql 事件中的幻觉修复记录，告诉用户 AI 做了哪些自动修正。

#### 事件序列示例

一次完整的缓存未命中查询，事件序列如下：

```json
{"event": "cache",    "data": {"hit": false, "duration_ms": 2}}
{"event": "intent",   "data": {"intent": "DataQuery", "detail": "识别为数据查询意图", "duration_ms": 150}}
{"event": "semantics","data": {"detail": "选择了 2 个表: t_orders, t_products", "tables": ["t_orders","t_products"], "duration_ms": 3200}}
{"event": "sql",      "data": {"sql": "SELECT ...", "detail": "生成 SQL: SELECT ...", "duration_ms": 2800}}
{"event": "data",     "data": {"success": true, "columns": ["city","amount"], "rows": [...], "row_count": 10, "duration_ms": 45}}
{"event": "chart",    "data": {"chart_type": "bar", "detail": "推荐图表: 柱状图"}}
{"event": "complete", "data": {"success": true, "is_slow": false, "cached": false}}
```

#### 动手练习

1. 在浏览器 DevTools 的 Network 面板中，找到 `/query/stream` 请求，查看 EventStream 标签页，观察每个事件的原始文本。

2. 在 `handleSSEEvent` 函数（`frontend/src/stores/chatStore.ts:222`）中加 `console.log(eventType, data)`，观察前端收到的事件序列。

---

### 6.5 SSE 消费 — 前端 EventSource → 后端 StreamingResponse 对接

#### 概念

SSE（Server-Sent Events）是一种基于 HTTP 长连接的服务器推送协议。
与 WebSocket 的区别：SSE 是单向的（服务器 → 客户端），更简单，自动重连，适合"服务器逐步推送进度"的场景。

ChatBI 的 SSE 对接链路：

```
前端 fetch() → 后端 @router.post("/stream") → StreamingResponse(event_stream())
     ↑                                                    ↓
  ReadableStream ← SSE 文本行 ← "event: xxx\ndata: {...}\n\n"
```

#### 后端：StreamingResponse

`backend/app/api/query.py:784-788`：

```python
return StreamingResponse(
    event_stream(),
    media_type="text/event-stream",
    headers={"X-Accel-Buffering": "no"},
)
```

逐行解读：

1. **`StreamingResponse(event_stream(), ...)`** — FastAPI 的流式响应类，接收一个异步生成器 `event_stream()`。
   每当生成器 yield 一段文本，StreamingResponse 就把它写入 HTTP 响应体并刷新缓冲区。
2. **`media_type="text/event-stream"`** — SSE 的标准 MIME 类型，浏览器和 CDN 据此识别这是 SSE 流。
3. **`headers={"X-Accel-Buffering": "no"}`** — 告诉 nginx 代理不要缓冲响应，直接透传到客户端。
   没有这个 header，nginx 可能会攒够一定量才发送，导致前端看到延迟。

#### 后端：event_stream() 内部

`backend/app/api/query.py:482-783` — `event_stream()` 是一个嵌套的 async generator：

```python
async def event_stream():
    nonlocal final_success, final_sql, final_error, ...

    # Step 0: Check cache
    cached = await cache_get(data.question, data.datasource_id, tenant_id)
    if cached:
        # ... 缓存命中路径，逐个 yield SSE 事件
        yield f"event: cache\ndata: {json.dumps({'hit': True, 'type': 'exact', ...}, ensure_ascii=False)}\n\n"
        # ...
        return

    # Cache miss — use shared pipeline executor
    from app.services.pipeline_executor import execute_query_pipeline
    async for event in execute_query_pipeline(data.question, data.datasource_id, tenant_id, data.history):
        yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
```

逐行解读：

1. **`nonlocal final_success, ...`** — 声明这些变量来自外层函数（`stream_query`），允许在 `event_stream` 内修改。
   用途：流结束后，外层函数需要这些值来写审计日志和缓存。
2. **`yield f"event: cache\ndata: {...}\n\n"`** — SSE 标准格式：`event:` 行指定事件类型，`data:` 行指定事件数据，两个换行符 `\n\n` 表示事件结束。
3. **`async for event in execute_query_pipeline(...)`** — 消费管线执行器的事件流，逐个转发为 SSE 格式。
4. **`json.dumps(..., ensure_ascii=False, default=str)`** — `ensure_ascii=False` 允许中文直接输出（不转义为 `\uXXXX`），`default=str` 把无法序列化的类型（如 datetime）转为字符串。

#### 前端：fetch + ReadableStream

`frontend/src/stores/chatStore.ts:141-196`：

```typescript
const response = await fetch(`${baseURL}/query/stream`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  },
  body: JSON.stringify({ question, datasource_id: currentDatasourceId.value, history: ... }),
})

const reader = response.body?.getReader()
if (!reader) throw new Error('Stream not available')

const decoder = new TextDecoder()
let buffer = ''

while (true) {
  const { done, value } = await reader.read()
  if (done) break

  buffer += decoder.decode(value, { stream: true })
  const lines = buffer.split('\n')
  buffer = lines.pop() || ''

  let i = 0
  while (i < lines.length) {
    if (lines[i].startsWith('event: ')) {
      const eventType = lines[i].slice(7).trim()
      i++
      if (i < lines.length && lines[i].startsWith('data: ')) {
        try {
          const data = JSON.parse(lines[i].slice(6))
          handleSSEEvent(eventType, data, assistantMsg)
        } catch {
          // Skip malformed JSON
        }
      }
    }
    i++
  }
}
```

逐行解读：

1. **`fetch(...)`** — 使用原生 fetch API 发起 POST 请求。不用 axios 是因为 axios 不原生支持流式读取。
2. **`response.body?.getReader()`** — 获取 ReadableStream 的 reader，用于逐块读取响应体。
   这是浏览器提供的 Streams API，与 Node.js 的 stream 不同。
3. **`decoder.decode(value, { stream: true })`** — 把 Uint8Array 解码为字符串。`stream: true` 表示可能有多字节字符被截断，解码器会记住未完成的字符，下次继续。
4. **`buffer += ...`** — 累积缓冲区。因为一次 `reader.read()` 可能读到不完整的一行（TCP 分包），需要缓冲到收到完整行再处理。
5. **`lines.pop() || ''`** — 最后一行可能不完整（不以 `\n` 结尾），放回缓冲区等下次拼接。
6. **`lines[i].startsWith('event: ')`** — 解析 SSE 格式：`event:` 行 + `data:` 行组成一个事件。
7. **`handleSSEEvent(eventType, data, assistantMsg)`** — 把解析后的事件交给状态管理函数处理，更新 UI。

#### 前端：handleSSEEvent 事件分发

`frontend/src/stores/chatStore.ts:222-373` — 核心分发逻辑（简化）：

```typescript
function handleSSEEvent(eventType: string, data: any, msg: Message): void {
  switch (eventType) {
    case 'cache':
      // 更新管线步骤：缓存命中/未命中
      msg.pipelineSteps = [{ type: 'intent', label: data.hit ? '缓存命中' : '缓存检查', status: 'done', ... }]
      break
    case 'intent':
      // 更新管线步骤：意图识别完成，Schema 选择开始
      msg.pipelineSteps.push({ type: 'intent', label: `意图识别: ${...}`, status: 'done', ... })
      msg.pipelineSteps.push({ type: 'semantics', label: 'Schema 选择', status: 'running' })
      break
    case 'sql':
      // 更新管线步骤：SQL 生成完成，执行开始
      msg.sql = data.sql
      break
    case 'data':
      // 更新查询结果
      msg.columns = data.columns
      msg.rows = data.rows
      msg.row_count = data.row_count
      break
    case 'chart':
      // 更新图表类型
      msg.chart_type = data.chart_type
      break
    case 'complete':
      // 标记所有 running 步骤为 done
      msg.pipelineSteps?.forEach(s => { if (s.status === 'running') s.status = 'done' })
      break
  }
  replaceMsgInArray(msg)  // 触发 Vue 3 响应式更新
}
```

#### 完整 SSE 对接流程图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              后端                                            │
│                                                                              │
│  @router.post("/stream")                                                     │
│       │                                                                      │
│       ▼                                                                      │
│  event_stream()  ← async generator                                          │
│       │                                                                      │
│       ├── await cache_get(...)                                               │
│       │       │                                                              │
│       │       ├── 命中 → yield "event: cache\ndata: {hit:true}\n\n"         │
│       │       └── 未命中 → yield "event: cache\ndata: {hit:false}\n\n"      │
│       │                                                                      │
│       └── async for event in execute_query_pipeline(...):                    │
│               │                                                              │
│               └── yield f"event: {event['event']}\ndata: {json}\n\n"        │
│                                                                              │
│  StreamingResponse(event_stream(), media_type="text/event-stream")           │
│       │                                                                      │
│       ▼  HTTP 响应体（SSE 文本流）                                           │
└───────┬──────────────────────────────────────────────────────────────────────┘
        │
        │  HTTP 长连接（text/event-stream）
        │
┌───────▼──────────────────────────────────────────────────────────────────────┐
│                              前端                                            │
│                                                                              │
│  fetch("/query/stream", {method: "POST", body: ...})                         │
│       │                                                                      │
│       ▼                                                                      │
│  response.body.getReader()                                                   │
│       │                                                                      │
│       ▼                                                                      │
│  while (true) {                                                              │
│    const {done, value} = await reader.read()                                 │
│    buffer += decoder.decode(value)                                           │
│    解析 "event: xxx\ndata: {...}\n\n" 格式                                   │
│       │                                                                      │
│       ▼                                                                      │
│    handleSSEEvent(eventType, data, assistantMsg)                             │
│       │                                                                      │
│       ├── 更新 msg.pipelineSteps（管线步骤状态）                              │
│       ├── 更新 msg.sql / msg.rows / msg.chart_type（查询结果）               │
│       └── replaceMsgInArray(msg) → 触发 Vue 3 响应式渲染                    │
│  }                                                                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### 动手练习

1. 在浏览器 DevTools 中打开 Network 面板，发起一次查询，找到 `/query/stream` 请求：
   - 查看 Headers 标签页：确认 `Content-Type: text/event-stream`
   - 查看 EventStream 标签页：观察每个事件的原始文本和时间线

2. 在 `chatStore.ts` 的 `handleSSEEvent` 中添加 `console.table` 输出：

```typescript
console.table({ eventType, duration_ms: data.duration_ms, detail: data.detail?.slice(0, 30) })
```

---

## 第 7 章：支撑服务解读

本章解读 ChatBI 的 6 个支撑服务模块。它们不直接参与 AI 推理，但为整个系统提供
缓存、连接池、安全、脱敏、审计等基础能力。理解这些服务，你才能回答：
"缓存是怎么工作的？""多数据源怎么管理？""手机号为什么显示成 138****5678？"

---

### 7.1 cache_service.py — 精确缓存 + 语义缓存 + Redis + 内存回退

#### 概念

`cache_service.py` 实现了两层缓存策略：

| 层级 | 实现方式 | 匹配精度 | 命中率 | 延迟 |
|-----|---------|---------|-------|------|
| 精确缓存 | Redis + SHA256 哈希 | 100%（文本完全一致） | 低（换个说法就 miss） | <1ms |
| 语义缓存 | Redis + 词重叠度 | ~80%（语义相似） | 中（"销量"≈"营收"） | ~5ms |

两层缓存都存储在 Redis 中，TTL 由 `settings.query_cache_ttl_seconds` 控制。
当 Redis 不可用时，所有缓存操作静默失败，不影响主流程（内存回退 = 无缓存）。

#### 真实代码

**缓存 key 生成** — `backend/app/services/cache_service.py:60-62`：

```python
def _cache_key(question: str, datasource_id: str, tenant_id: str = "") -> str:
    raw = f"{tenant_id}:{question.strip().lower()}:{datasource_id}"
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()}"
```

**精确缓存读取** — `backend/app/services/cache_service.py:71-84`：

```python
async def cache_get(question: str, datasource_id: str, tenant_id: str = "") -> dict | None:
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw:
            cache_stats.record_hit()
            logger.info("Cache HIT for key %s", key[:16])
            return json.loads(raw)
        cache_stats.record_miss()
    except Exception as e:
        logger.warning("Redis cache get failed: %s", e)
        cache_stats.record_miss()
    return None
```

**精确缓存写入** — `backend/app/services/cache_service.py:87-110`：

```python
async def cache_set(question: str, datasource_id: str, result: dict, tenant_id: str = "", ttl: int | None = None) -> None:
    if not result.get("success") or not result.get("rows"):
        cache_stats.record_skipped()
        return
    key = _cache_key(question, datasource_id, tenant_id)
    effective_ttl = ttl or _resolve_ttl(datasource_id, result)
    try:
        redis = await get_redis()
        await redis.setex(key, effective_ttl, json.dumps(result, default=str))
        cache_stats.record_set()
        # Index key in per-datasource set for targeted cache invalidation
        index_key = _datasource_index_key(datasource_id, tenant_id)
        await redis.sadd(index_key, key)
        await redis.expire(index_key, effective_ttl)
        # Also index for semantic lookup
        _add_to_semantic_index(question, key, datasource_id, tenant_id)
    except Exception as e:
        logger.warning("Redis cache set failed: %s", e)
```

**语义缓存查找** — `backend/app/services/cache_service.py:203-232`：

```python
async def semantic_cache_get(question: str, datasource_id: str, tenant_id: str = "", threshold: float = 0.8) -> dict | None:
    try:
        redis = await get_redis()
        index_key = _semantic_cache_key(question, datasource_id, tenant_id)
        raw = await redis.get(index_key)
        if not raw:
            return None
        index = json.loads(raw)
        best_score = 0.0
        best_key = None
        for entry in index:
            score = _simple_similarity(question, entry["q"])
            if score > best_score:
                best_score = score
                best_key = entry["k"]
        if best_score >= threshold and best_key:
            cached = await redis.get(best_key)
            if cached:
                cache_stats.record_hit(semantic=True)
                result = json.loads(cached)
                result["_semantic_match"] = True
                return result
    except Exception as e:
        logger.warning("Semantic cache lookup failed: %s", e)
    return None
```

**词重叠度计算** — `backend/app/services/cache_service.py:194-200`：

```python
def _simple_similarity(a: str, b: str) -> float:
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))
```

#### 逐行解读

1. **`f"{tenant_id}:{question.strip().lower()}:{datasource_id}"`** — 缓存 key 的原始字符串，包含租户 ID、问题文本（去空格转小写）、数据源 ID。
   租户 ID 在前，确保不同租户的相同问题不会互相命中。
2. **`hashlib.sha256(raw.encode()).hexdigest()`** — SHA256 哈希。为什么哈希？(1) 原文可能很长，Redis key 越短性能越好；(2) 原文可能含特殊字符，哈希后统一为十六进制字符串。
3. **`if not result.get("success") or not result.get("rows"):`** — 只缓存成功的、有数据的查询结果。失败或空结果不缓存，避免下次返回同样的错误。
4. **`await redis.setex(key, effective_ttl, ...)`** — `setex` = SET with EXpiry，原子操作设置 key + 过期时间。
5. **`await redis.sadd(index_key, key)`** — 把缓存 key 加入数据源索引集合，用于按数据源批量清除缓存。
6. **`_simple_similarity(a, b)`** — Jaccard 系数的变体：交集大小 / 最大集合大小。阈值 0.8 表示 80% 的词重叠才认为语义相似。
7. **`result["_semantic_match"] = True`** — 标记这是语义缓存命中，前端可以显示"语义缓存命中"而非"精确缓存命中"。

#### 缓存统计

`backend/app/services/cache_service.py:20-57` — `CacheStats` 类：

```python
class CacheStats:
    def __init__(self):
        self.hits: int = 0
        self.misses: int = 0
        self.semantic_hits: int = 0
        self.sets: int = 0
        self.skipped: int = 0

    def snapshot(self) -> dict:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0
        return {"hits": self.hits, "misses": self.misses, "semantic_hits": self.semantic_hits,
                "sets": self.sets, "skipped": self.skipped, "hit_rate": round(hit_rate, 4)}
```

这是一个线程安全的内存计数器（GIL 保证），记录缓存命中率，供监控面板使用。

#### 动手练习

1. 查看缓存统计：

```python
from app.services.cache_service import cache_stats
print(cache_stats.snapshot())
# {'hits': 42, 'misses': 18, 'semantic_hits': 7, 'sets': 35, 'skipped': 3, 'hit_rate': 0.7}
```

2. 测试语义缓存相似度：

```python
from app.services.cache_service import _simple_similarity
print(_simple_similarity("各城市的订单数量", "按城市统计订单数"))  # ~0.67
print(_simple_similarity("上个月销售额", "上月营收"))            # ~0.0 (中文分词问题)
```

---

### 7.2 connection_pool.py — 多数据源动态引擎管理

#### 概念

ChatBI 支持多个数据源（不同数据库实例），每个数据源需要独立的连接池。
`ConnectionPoolManager` 用一个字典 `_pools: dict[str, AsyncEngine]` 管理所有连接池，
按数据源 ID 索引，首次访问时创建，后续复用。

#### 真实代码

**URL 构建** — `backend/app/services/connection_pool.py:16-41`：

```python
def _build_url(ds: DataSource) -> URL:
    username = quote_plus(decrypt_value(ds.username_encrypted))
    password = quote_plus(decrypt_value(ds.password_encrypted))
    if ds.db_type == "postgresql":
        return URL.create("postgresql+asyncpg", username=username, password=password, host=ds.host, port=ds.port, database=ds.database_name)
    elif ds.db_type == "sqlite":
        return URL.create("sqlite+aiosqlite", database=ds.database_name)
    else:
        return URL.create("mysql+aiomysql", username=username, password=password, host=ds.host, port=ds.port, database=ds.database_name, query={"charset": "utf8mb4"})
```

**连接池获取** — `backend/app/services/connection_pool.py:47-71`：

```python
class ConnectionPoolManager:
    _pools: dict[str, AsyncEngine] = {}

    async def get_pool(self, ds: DataSource) -> AsyncEngine:
        ds_id = str(ds.id)
        if ds_id in self._pools:
            return self._pools[ds_id]
        url = _build_url(ds)
        pool_size = getattr(settings, 'pool_min_size', settings.db_pool_size)
        max_overflow = getattr(settings, 'pool_max_size', settings.db_pool_max_overflow)
        engine = create_async_engine(
            url.render_as_string(hide_password=False),
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
        )
        self._pools[ds_id] = engine
        return engine
```

**健康检查** — `backend/app/services/connection_pool.py:119-126`：

```python
async def health_check(self, ds_id: str, ds: DataSource) -> dict:
    try:
        engine = await self.get_pool(ds)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"healthy": True, "error": None}
    except Exception as e:
        return {"healthy": False, "error": "连接失败"}
```

#### 逐行解读

1. **`quote_plus(decrypt_value(ds.username_encrypted))`** — 先用 Fernet 解密数据库密码（存储时加密），再用 `quote_plus` URL 编码（防止密码含特殊字符如 `@` 导致 URL 解析错误）。
2. **`URL.create(...)`** — SQLAlchemy 的安全 URL 构建方法，比字符串拼接更可靠（自动处理编码和转义）。
3. **`if ds_id in self._pools:`** — 先查缓存，避免重复创建引擎。引擎创建开销很大（建立 TCP 连接、初始化连接池）。
4. **`create_async_engine(..., pool_size=..., max_overflow=...)`** — 创建异步引擎。`pool_size` 是常驻连接数，`max_overflow` 是允许临时超出的连接数，`pool_recycle` 是连接最大存活时间（防止 MySQL 8 小时断连问题）。
5. **`_pools: dict[str, AsyncEngine] = {}`** — 类变量，所有实例共享同一个字典。项目用全局单例 `pool_manager = ConnectionPoolManager()` 确保唯一。
6. **`await conn.execute(text("SELECT 1"))`** — 健康检查：执行最轻量的 SQL，验证连接是否可用。

#### 支持的数据库类型

| db_type | 驱动 | 用途 |
|---------|------|------|
| `mysql`（默认） | `mysql+aiomysql` | 主力数据库 |
| `postgresql` | `postgresql+asyncpg` | PostgreSQL 数据源 |
| `sqlite` | `sqlite+aiosqlite` | 测试/轻量数据源 |

#### 动手练习

1. 查看当前活跃的连接池：

```python
from app.services.connection_pool import pool_manager
print(pool_manager._pools.keys())  # 所有已创建的连接池
status = await pool_manager.get_pool_status()  # 每个池的连接数
```

2. 测试数据源健康检查：

```python
result = await pool_manager.health_check("datasource-id", ds_obj)
print(result)  # {'healthy': True, 'error': None}
```

---

### 7.3 query_complexity.py + simple_query_executor.py — 简单查询走小模型

#### 概念

不是所有查询都需要大模型。像"有多少用户"这种简单问题，用更小、更快、更便宜的模型就够了。
`query_complexity.py` 用关键词启发式估算复杂度，`simple_query_executor.py` 用小模型走简化路径。

| 复杂度 | 分数范围 | 模型路径 | 典型问题 |
|--------|---------|---------|---------|
| simple | 0-2 | 小模型（跳过 Schema 选择） | "用户总数"、"订单列表" |
| normal | 3-5 | 完整管线 | "按月份统计销售额" |
| complex | 6+ | 完整管线 | "同比环比分析"、"漏斗转化率" |

#### 真实代码

**复杂度估算** — `backend/app/services/query_complexity.py:22-93`：

```python
_SIMPLE_METRICS = {"数量", "总数", "合计", "总和", "平均", "最大", "最小", "列表", "明细"}
_SIMPLE_DIMENSIONS = {"按", "分组", "分组统计"}
_COMPLEX_KEYWORDS = {"同比", "环比", "排名", "排行", "top", "top10", "占比", "渗透率", "转化率", "漏斗", "连续", "趋势", "波动", "异常", "对比", "比较", "差异"}
_TIME_KEYWORDS = {"月", "季度", "年", "周", "日", "上月", "今年", "去年", "同期", "最近"}

def estimate_query_complexity(question: str) -> dict:
    q = question.lower().strip()
    score = 0
    reasons: list[str] = []

    if any(kw in question for kw in _SIMPLE_METRICS):
        score += 1
        reasons.append("contains simple metric keyword")
    if any(kw in question for kw in _SIMPLE_DIMENSIONS):
        score += 1
        reasons.append("contains dimension/group keyword")
    found_complex = [kw for kw in _COMPLEX_KEYWORDS if kw in q]
    if found_complex:
        score += 3 * len(found_complex)
        reasons.append(f"complex keywords: {', '.join(found_complex)}")
    # ... 更多评分规则 ...

    if score <= 2:
        level = "simple"
    elif score <= 5:
        level = "normal"
    else:
        level = "complex"

    return {"level": level, "score": score, "reasons": reasons}
```

**简单查询执行** — `backend/app/services/simple_query_executor.py:68-199`：

```python
async def execute_simple_query(question: str, datasource_id: str, tenant_id: str) -> dict:
    start = time.monotonic()

    # Fetch metadata
    async with async_session_factory() as db:
        query = select(MetadataConfig).where(MetadataConfig.datasource_id == datasource_id, MetadataConfig.tenant_id == tenant_id)
        config_result = await db.execute(query)
        config = config_result.scalar_one_or_none()
        raw_metadata = config.config if config else ""

    # Build minimal schema context (no LLM selection)
    schema_context = await _build_minimal_schema(raw_metadata)

    # Generate SQL with simple model
    llm = _get_simple_llm()
    messages = [
        ("system", _SIMPLE_SYSTEM_PROMPT),
        ("human", f"问题: {question}\n\n表结构:\n{schema_context}"),
    ]
    async with asyncio.timeout(20):
        response = await llm.ainvoke(messages)

    raw_sql = response.content.strip()
    # ... 清理 markdown 标记，提取 SELECT ...

    # Execute SQL
    result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)

    # Self-heal on failure
    if not result.get("success"):
        heal_result = await self_heal_sql(question=question, sql=sql, error=result.get("error", ""), ...)
        if heal_result.get("success"):
            sql = heal_result.get("sql", sql)
            result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)

    return {...}
```

#### 逐行解读

1. **`_SIMPLE_METRICS`** — 简单指标关键词集合。包含这些词的问题通常是单表聚合查询，不需要复杂 Schema 选择。
2. **`score += 3 * len(found_complex)`** — 复杂关键词每个加 3 分。"同比"+"排名" = 6 分，直接归入 complex。
3. **`_build_minimal_schema(raw_metadata)`** — 构建精简的 Schema 上下文。只取表名 + 说明 + 前 15 个字段，比完整 Schema 小得多，减少 token 消耗。
4. **`_get_simple_llm()`** — 获取小模型实例（`settings.llm_simple_model`），max_tokens=500，temperature=0.0。
5. **`asyncio.timeout(20)`** — 20 秒超时，比完整管线的 60 秒更短。简单问题不应该花太长时间。
6. **即使简单路径也支持自愈** — `self_heal_sql` 在简单路径中同样可用，保证查询成功率。

#### 路由决策流程

```
用户提问
    │
    ▼
estimate_query_complexity()
    │
    ├── score <= 2 → simple 路径
    │     │
    │     ▼
    │   execute_simple_query()
    │     ├── 获取元数据（无 LLM）
    │     ├── 构建精简 Schema（无 LLM）
    │     ├── 小模型生成 SQL（1 次 LLM 调用）
    │     ├── 执行 SQL
    │     └── 失败则自愈（1 次 LLM 调用）
    │
    └── score > 2 → 完整管线
          ├── 意图识别（1 次 LLM）
          ├── Schema 选择（2 次 LLM）
          ├── SQL 生成（1 次 LLM）
          ├── 执行 SQL
          └── 失败则自愈（1 次 LLM）
```

简单路径最多 2 次 LLM 调用，完整管线最多 5 次。对于简单问题，延迟从 ~8 秒降到 ~3 秒。

#### 动手练习

1. 测试复杂度估算：

```python
from app.services.query_complexity import estimate_query_complexity
print(estimate_query_complexity("用户总数"))           # {'level': 'simple', 'score': 1}
print(estimate_query_complexity("按月份统计销售额"))    # {'level': 'normal', 'score': 3}
print(estimate_query_complexity("各城市同比环比分析"))  # {'level': 'complex', 'score': 7}
```

2. 在 `config.py` 中设置 `LLM_SIMPLE_MODEL`（如 `gpt-4o-mini`），观察简单查询的延迟差异。

---

### 7.4 data_masking.py — 手机号/身份证正则脱敏

#### 概念

查询结果可能包含敏感数据（手机号、身份证、邮箱等），`data_masking.py` 在返回给前端之前自动脱敏。
脱敏规则基于两重匹配：列名匹配（判断哪些列是敏感的）+ 值正则匹配（判断值的格式并应用掩码）。

#### 真实代码

**脱敏规则** — `backend/app/services/data_masking.py:11-18`：

```python
MASKING_RULES = [
    # Chinese phone: 13812345678 -> 138****5678
    (re.compile(r"^1[3-9]\d{9}$"), lambda v: v[:3] + "****" + v[-4:]),
    # Chinese ID: 18 digits -> ***************1234
    (re.compile(r"^\d{17}[\dXx]$"), lambda v: "***************" + v[-4:]),
    # Email: john@example.com -> j***n@example.com
    (re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"), lambda v: v[0] + "***@" + v.split("@")[1]),
]
```

**敏感列名模式** — `backend/app/services/data_masking.py:21-27`：

```python
SENSITIVE_COLUMN_PATTERNS = [
    "phone", "mobile", "手机号", "电话", "联系电话",
    "id_card", "idcard", "身份证", "证件号",
    "email", "邮箱", "邮件",
    "bank_card", "bankcard", "银行卡", "卡号",
    "password", "密码", "secret", "token",
]
```

**主函数** — `backend/app/services/data_masking.py:43-72`：

```python
def mask_sensitive_data(columns: list[str], rows: list[dict]) -> tuple[list[str], list[dict]]:
    sensitive_cols = {col for col in columns if _is_sensitive_column(col)}
    if not sensitive_cols:
        return columns, rows

    masked_rows = []
    for row in rows:
        masked_row = {}
        for col, value in row.items():
            if col in sensitive_cols and isinstance(value, str) and value:
                masked_row[col] = _mask_value(value)
            else:
                masked_row[col] = value
        masked_rows.append(masked_row)

    return columns, masked_rows
```

#### 逐行解读

1. **`re.compile(r"^1[3-9]\d{9}$")`** — 中国手机号正则：1 开头，第二位 3-9，共 11 位。`^` 和 `$` 确保完整匹配，不会误脱敏包含数字的地址字段。
2. **`lambda v: v[:3] + "****" + v[-4:]`** — 手机号脱敏：保留前 3 位和后 4 位，中间用 `****` 替换。13812345678 → 138****5678。
3. **`lambda v: "***************" + v[-4:]`** — 身份证脱敏：只保留后 4 位。110101199001011234 → ***************1234。
4. **`_is_sensitive_column(col_name)`** — 列名匹配：检查列名是否包含 `phone`、`手机号` 等关键词。用 `any(kw in col_lower for kw in SENSITIVE_COLUMN_PATTERNS)` 做子串匹配。
5. **`if col in sensitive_cols and isinstance(value, str) and value:`** — 三重条件：列是敏感列 + 值是字符串 + 值非空。数字类型的手机号不会被脱敏（需要先转为字符串）。
6. **`_mask_value(value)`** — 依次尝试三条正则规则，匹配到就应用对应的脱敏函数，都不匹配则原样返回。

#### 脱敏效果示例

| 原始值 | 列名 | 脱敏后 | 规则 |
|--------|------|--------|------|
| 13812345678 | phone | 138****5678 | 手机号正则 |
| 110101199001011234 | id_card | \*\*\*\*\*\*\*\*\*\*\*\*\*\*\*1234 | 身份证正则 |
| john@example.com | email | j\*\*\*@example.com | 邮箱正则 |
| 北京市朝阳区 | address | 北京市朝阳区 | 无匹配规则，原样返回 |

#### 动手练习

1. 直接调用脱敏函数测试：

```python
from app.services.data_masking import mask_sensitive_data
columns = ["name", "phone", "id_card"]
rows = [{"name": "张三", "phone": "13812345678", "id_card": "110101199001011234"}]
cols, masked = mask_sensitive_data(columns, rows)
print(masked)  # [{'name': '张三', 'phone': '138****5678', 'id_card': '***************1234'}]
```

2. 添加新的脱敏规则（如银行卡号）：

```python
# 在 MASKING_RULES 中添加
(re.compile(r"^\d{16,19}$"), lambda v: "***********" + v[-4:]),
```

---

### 7.5 security.py — JWT 签发/验证 + bcrypt + 邮箱验证 token

#### 概念

`security.py` 是 ChatBI 的安全核心，提供三类安全能力：

| 能力 | 实现 | 用途 |
|------|------|------|
| 密码哈希 | bcrypt（cost=12） | 用户注册/登录 |
| JWT 令牌 | python-jose（HS256） | API 鉴权 |
| 时间令牌 | itsdangerous（URLSafeTimedSerializer） | 密码重置/邮箱验证 |

#### 真实代码

**密码哈希** — `backend/app/core/security.py:16-24`：

```python
def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.bcrypt_rounds),
    ).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
```

**JWT 签发** — `backend/app/core/security.py:29-38`：

```python
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload = {**data, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)

def create_refresh_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload = {**data, "type": "refresh", "exp": expire, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
```

**JWT 验证** — `backend/app/core/security.py:41-56`：

```python
def verify_access_token(token: str) -> Optional[dict]:
    return _verify_token(token, expected_type="access")

def _verify_token(token: str, expected_type: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except JWTError:
        return None
```

**FastAPI 鉴权依赖** — `backend/app/core/security.py:95-109`：

```python
async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "UNAUTHORIZED", "message": "未提供认证令牌", "details": None})
    token = auth.split(" ", 1)[1]
    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "INVALID_TOKEN", "message": "无效的访问令牌", "details": None})
    return payload
```

**角色权限检查** — `backend/app/core/security.py:112-122`：

```python
def require_role(*allowed_roles: str):
    async def _check(user=Depends(get_current_user), db: AsyncSession = Depends(lambda: None)) -> dict:
        role = user.get("role", "user")
        if role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "FORBIDDEN", "message": f"需要 {', '.join(allowed_roles)} 权限", "details": None})
        return user
    return _check
```

**邮箱验证令牌** — `backend/app/core/security.py:137-144`：

```python
def generate_email_verification_token(email: str) -> str:
    return _get_serializer().dumps(email)

def verify_email_verification_token(token: str) -> Optional[str]:
    try:
        return _get_serializer().loads(token, max_age=86400)  # 24 小时有效
    except (SignatureExpired, BadSignature):
        return None
```

#### 逐行解读

1. **`bcrypt.gensalt(rounds=settings.bcrypt_rounds)`** — 生成盐值。`rounds=12` 表示 2^12 = 4096 次哈希迭代，耗时约 250ms，足够抵抗暴力破解。
2. **`{**data, "type": "access", "exp": expire}`** — JWT payload。`**data` 解包用户信息（user_id, email, tenant_id, role），再添加 `type`（区分 access/refresh）和 `exp`（过期时间）。
3. **`"jti": str(uuid.uuid4())`** — refresh token 的唯一标识，用于令牌撤销（黑名单机制）。
4. **`payload.get("type") != expected_type`** — 类型校验：防止用 refresh token 冒充 access token。
5. **`auth.split(" ", 1)[1]`** — 从 `Authorization: Bearer xxx` 中提取 token。`split(" ", 1)` 只分割一次，防止 token 中含空格。
6. **`require_role(*allowed_roles: str)`** — 工厂函数，返回一个 FastAPI 依赖。用法：`Depends(require_role("admin", "user"))`。
7. **`_get_serializer().loads(token, max_age=86400)`** — itsdangerous 的时间令牌验证。`max_age=86400` 表示 24 小时有效，过期抛出 `SignatureExpired`。

#### JWT 令牌结构

```json
{
  "user_id": "uuid-xxx",
  "email": "user@example.com",
  "tenant_id": "uuid-yyy",
  "role": "admin",
  "type": "access",
  "exp": 1715299200
}
```

所有 API 路由通过 `Depends(get_current_user)` 自动解析 JWT，获取当前用户信息。

#### 动手练习

1. 手动签发和验证 JWT：

```python
from app.core.security import create_access_token, verify_access_token
token = create_access_token({"user_id": "test", "email": "test@test.com", "tenant_id": "t1", "role": "admin"})
print(token)  # eyJhbGciOiJIUzI1NiIs...
payload = verify_access_token(token)
print(payload)  # {'user_id': 'test', 'email': 'test@test.com', ...}
```

2. 用过期 token 测试验证失败：

```python
from datetime import timedelta
token = create_access_token({"user_id": "test"}, expires_delta=timedelta(seconds=-1))
print(verify_access_token(token))  # None
```

---

### 7.6 audit_service.py + analytics_service.py — 审计记录 + 使用统计

#### 概念

ChatBI 有两套互补的追踪系统：

| 服务 | 用途 | 存储位置 | 典型查询 |
|------|------|---------|---------|
| `audit_service.py` | 合规审计：谁在什么时候做了什么 | MySQL `audit_logs` 表 | "过去 24 小时的慢查询有哪些？" |
| `analytics_service.py` | 产品分析：用户行为统计 | MySQL `analytics_events` 表 | "查询成功率是多少？" |

#### 真实代码

**审计日志记录** — `backend/app/services/audit_service.py:14-54`：

```python
async def log_action(
    db: AsyncSession, tenant_id: str, user_id: str, action: str, resource_type: str,
    resource_id: str = "", details: str = "", sql_text: str | None = None,
    result_count: int | None = None, execution_time_ms: int | None = None,
    sql_execution_time_ms: int | None = None, error_message: str | None = None,
    conversation_id: str | None = None,
) -> None:
    audit = AuditLog(id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, action=action, ...)
    db.add(audit)

    if execution_time_ms and execution_time_ms > settings.slow_query_threshold_ms:
        audit.is_slow = True
        audit.details = f"[SLOW:{execution_time_ms}ms] {details}" if details else f"[SLOW:{execution_time_ms}ms]"
        logger.warning("Slow query detected: %dms (SQL: %sms), action=%s, user=%s, sql=%s",
                       execution_time_ms, sql_execution_time_ms or "N/A", action, user_id, (sql_text or "")[:200])
```

**慢查询列表** — `backend/app/services/audit_service.py:57-110`：

```python
async def get_slow_queries(db: AsyncSession, tenant_id: str, limit: int = 50, hours: int = 24) -> list[dict]:
    cutoff = datetime.now() - timedelta(hours=hours)
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.execution_time_ms > settings.slow_query_threshold_ms, AuditLog.created_at >= cutoff)
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()
    # ... 关联数据源名称 ...
```

**慢查询统计** — `backend/app/services/audit_service.py:126-154`：

```python
async def get_slow_query_stats(db: AsyncSession, tenant_id: str, hours: int = 24) -> dict:
    stmt = select(
        func.count().label("total"),
        func.avg(AuditLog.execution_time_ms).label("avg_ms"),
        func.max(AuditLog.execution_time_ms).label("max_ms"),
    ).where(AuditLog.tenant_id == tenant_id, AuditLog.execution_time_ms > settings.slow_query_threshold_ms, AuditLog.created_at >= cutoff)
    result = await db.execute(stmt)
    row = result.one()
    return {"total_slow_queries": row.total or 0, "avg_execution_time_ms": int(row.avg_ms or 0), ...}
```

**分析事件追踪** — `backend/app/services/analytics_service.py:36-52`：

```python
EVENT_USER_LOGIN = "user_login"
EVENT_QUERY_EXECUTE = "query_execute"
EVENT_QUERY_SUCCESS = "query_success"
EVENT_QUERY_ERROR = "query_error"
EVENT_CHART_VIEW = "chart_view"
EVENT_FEEDBACK_SUBMIT = "feedback_submit"
# ... 共 14 种事件 ...

async def track_event(db: AsyncSession, tenant_id: str, user_id: str, event_name: str, event_data: dict[str, Any] | None = None) -> None:
    event = AnalyticsEvent(tenant_id=tenant_id, user_id=user_id, event_name=event_name, event_data=json.dumps(event_data or {}, ensure_ascii=False))
    db.add(event)
```

#### 逐行解读

1. **`db.add(audit)`** — 只添加到会话，不 commit。commit 由调用方统一执行，确保审计日志和业务操作在同一个事务中。
2. **`execution_time_ms > settings.slow_query_threshold_ms`** — 慢查询自动标记。阈值由 `settings.slow_query_threshold_ms` 控制，默认 5000ms。
3. **`audit.details = f"[SLOW:{execution_time_ms}ms] {details}"`** — 慢查询的 details 前缀加 `[SLOW:Xms]` 标记，方便日志搜索。
4. **`logger.warning("Slow query detected: ...")`** — 慢查询记 WARNING 级别日志，运维可以通过日志告警系统及时发现。
5. **`func.count()`, `func.avg()`, `func.max()`** — SQLAlchemy 的聚合函数，在数据库层面计算，不把所有记录拉到 Python。
6. **`EVENT_QUERY_SUCCESS`, `EVENT_QUERY_ERROR`** — 成功/失败事件对，用于计算查询成功率。
7. **`json.dumps(event_data or {}, ensure_ascii=False)`** — 事件数据存为 JSON 字符串，`ensure_ascii=False` 保留中文。

#### 审计日志字段

| 字段 | 类型 | 含义 |
|------|------|------|
| `action` | str | 操作类型：QUERY_EXECUTE, RAW_QUERY_EXECUTE, SQL_EXPLAIN 等 |
| `resource_type` | str | 资源类型：query, datasource 等 |
| `resource_id` | str | 资源 ID（通常是数据源 ID） |
| `sql_text` | str | 执行的 SQL |
| `execution_time_ms` | int | 总耗时（AI 推理 + SQL 执行） |
| `sql_execution_time_ms` | int | 纯 SQL 执行耗时 |
| `is_slow` | bool | 是否慢查询 |
| `conversation_id` | str | 关联的对话 ID |

#### 动手练习

1. 查询最近的审计日志：

```python
from sqlalchemy import select, desc
from app.db.models import AuditLog
stmt = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(10)
result = await db.execute(stmt)
for log in result.scalars().all():
    print(f"{log.action} by {log.user_id}: {log.execution_time_ms}ms")
```

2. 获取慢查询统计：

```python
from app.services.audit_service import get_slow_query_stats
stats = await get_slow_query_stats(db, tenant_id="xxx", hours=24)
print(stats)  # {'total_slow_queries': 3, 'avg_execution_time_ms': 8500, 'max_execution_time_ms': 12000}
```

---

## 附录

---

### 附录 A：技术栈速查表

| 技术 | 版本 | 项目里哪里用了 | 对应章节 |
|------|------|--------------|---------|
| **Python** | 3.12 | 整个后端 | 全书 |
| **FastAPI** | - | `backend/app/main.py` — API 入口 | 第 6 章 |
| **SQLAlchemy** | async | `backend/app/db/` — ORM + 异步会话 | 第 7 章 7.2 |
| **aiomysql** | - | `backend/app/services/connection_pool.py:34` — MySQL 异步驱动 | 第 7 章 7.2 |
| **asyncpg** | - | `backend/app/services/connection_pool.py:23` — PostgreSQL 异步驱动 | 第 7 章 7.2 |
| **LangGraph** | - | `backend/app/ai/graph.py` — 状态图编排 | 第 6 章 6.3 |
| **LangChain** | - | `backend/app/ai/nodes/shared_utils.py` — LLM 调用封装 | 第 6 章 6.3 |
| **ChatOpenAI** | - | `backend/app/ai/nodes/intent.py:33` — OpenAI 兼容 API | 第 6 章 6.3 |
| **SQLGlot** | - | `backend/app/ai/nodes/execution.py:38` — SQL AST 校验 | 第 6 章 6.2 |
| **Redis** | 7 | `backend/app/core/redis_client.py` — 缓存/限流/登录锁 | 第 7 章 7.1 |
| **bcrypt** | - | `backend/app/core/security.py:4` — 密码哈希 | 第 7 章 7.5 |
| **python-jose** | - | `backend/app/core/security.py:6` — JWT 签发/验证 | 第 7 章 7.5 |
| **itsdangerous** | - | `backend/app/core/security.py:7` — 时间令牌 | 第 7 章 7.5 |
| **Fernet** | - | `backend/app/core/encryption.py` — 数据源密码加密 | 第 7 章 7.2 |
| **Vue 3** | - | `frontend/src/` — 整个前端 | 第 6 章 6.5 |
| **TypeScript** | - | `frontend/src/stores/chatStore.ts` — 状态管理 | 第 6 章 6.5 |
| **Pinia** | - | `frontend/src/stores/` — 全局状态 | 第 6 章 6.5 |
| **Element Plus** | - | `frontend/src/views/` — UI 组件库 | 第 6 章 6.5 |
| **ECharts** | - | `frontend/src/components/ChartRenderer.vue` — 图表渲染 | 第 6 章 6.4 |
| **fetch API** | - | `frontend/src/stores/chatStore.ts:141` — SSE 流式读取 | 第 6 章 6.5 |
| **ReadableStream** | - | `frontend/src/stores/chatStore.ts:166` — 流式响应消费 | 第 6 章 6.5 |
| **Docker** | - | `docker-compose.yml` — 容器编排 | - |
| **nginx** | - | `deploy/nginx.conf` — 反向代理 + SSE 透传 | 第 6 章 6.5 |

---

### 附录 B：关键文件索引

| 文件路径 | 职责 | 行数 | 对应章节 |
|---------|------|------|---------|
| `backend/app/services/pipeline_executor.py` | 管线执行器 — async generator 逐步产出 SSE 事件 | 361 | 第 6 章 6.1-6.4 |
| `backend/app/api/query.py` | 查询 API — 同步/SSE/异步/CSV 导出 | 1254 | 第 6 章 6.5 |
| `backend/app/ai/graph.py` | LangGraph 状态图 — 节点 + 边 + 条件路由 | 293 | 第 6 章 6.3 |
| `backend/app/ai/state.py` | QueryState 定义 — LangGraph 共享状态 | 170 | 第 6 章 6.3 |
| `backend/app/ai/nodes/intent.py` | 意图分类 — 三层策略（缓存/关键词/LLM） | 266 | 第 6 章 6.2-6.3 |
| `backend/app/ai/nodes/schema_selection.py` | Schema 选择 — 两步 LLM 交互 | ~300 | 第 6 章 6.3 |
| `backend/app/ai/nodes/generation.py` | SQL 生成 — 三次降级重试 + 幻觉修复 | ~250 | 第 6 章 6.3 |
| `backend/app/ai/nodes/execution.py` | SQL 执行 — AST 校验 + 只读保护 + 超时 | 236 | 第 6 章 6.2-6.3 |
| `backend/app/ai/nodes/self_heal.py` | SQL 自愈 — 迭代重试修复 | 267 | 第 6 章 6.3 |
| `backend/app/ai/nodes/context_resolver.py` | 上下文补全 — 追问解析 | ~150 | 第 6 章 6.3 |
| `backend/app/ai/chart_type.py` | 图表推断 — 纯规则引擎 | ~200 | 第 6 章 6.4 |
| `backend/app/services/cache_service.py` | 缓存服务 — 精确缓存 + 语义缓存 | 254 | 第 7 章 7.1 |
| `backend/app/services/connection_pool.py` | 连接池管理 — 多数据源动态引擎 | 130 | 第 7 章 7.2 |
| `backend/app/services/query_complexity.py` | 复杂度估算 — 关键词启发式 | 94 | 第 7 章 7.3 |
| `backend/app/services/simple_query_executor.py` | 简单查询执行 — 小模型路径 | 200 | 第 7 章 7.3 |
| `backend/app/services/data_masking.py` | 数据脱敏 — 正则匹配 + 掩码 | 73 | 第 7 章 7.4 |
| `backend/app/core/security.py` | 安全核心 — JWT + bcrypt + 时间令牌 | 123 | 第 7 章 7.5 |
| `backend/app/services/audit_service.py` | 审计服务 — 慢查询检测 + 统计 | 155 | 第 7 章 7.6 |
| `backend/app/services/analytics_service.py` | 分析服务 — 事件追踪 | 83 | 第 7 章 7.6 |
| `backend/app/core/config.py` | 全局配置 — 环境变量绑定 | ~200 | 全书 |
| `frontend/src/stores/chatStore.ts` | 聊天状态 — SSE 消费 + 事件分发 | 721 | 第 6 章 6.5 |
| `frontend/src/views/ChatView.vue` | 聊天页面 — 管线可视化 + 图表渲染 | ~400 | 第 6 章 6.5 |

---

### 附录 C：数据流全图

以下是从用户提问到返回图表的完整 ASCII 路径，标注每个代码文件和关键行号。

```
═══════════════════════════════════════════════════════════════════════════════════════
  ChatBI 完整数据流 — 从用户提问到返回图表
═══════════════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────────────────────────────────────────────────────────────┐
  │  前端 (Vue 3 + TypeScript)                                                     │
  │                                                                                │
  │  用户点击"发送"                                                                 │
  │       │                                                                        │
  │       ▼                                                                        │
  │  chatStore.ts:104  sendQuestion(question)                                      │
  │       │                                                                        │
  │       ├── 创建 assistantMsg 占位消息 (pipelineSteps: [])                       │
  │       │                                                                        │
  │       ▼                                                                        │
  │  chatStore.ts:141  fetch("/query/stream", {method: "POST", body: ...})         │
  │       │                                                                        │
  │       ▼                                                                        │
  │  chatStore.ts:166  response.body.getReader() → ReadableStream                  │
  │       │                                                                        │
  │       ▼  逐块读取 + 解析 SSE 格式                                               │
  │  chatStore.ts:182  解析 "event: xxx\ndata: {...}\n\n"                          │
  │       │                                                                        │
  │       ▼                                                                        │
  │  chatStore.ts:222  handleSSEEvent(eventType, data, assistantMsg)               │
  │       │                                                                        │
  │       ├── cache   → 更新管线步骤: 缓存命中/未命中                               │
  │       ├── intent  → 更新管线步骤: 意图识别完成                                  │
  │       ├── semantics → 更新管线步骤: Schema 选择完成                             │
  │       ├── sql     → 更新 msg.sql + 管线步骤                                    │
  │       ├── data    → 更新 msg.rows, msg.columns + 管线步骤                      │
  │       ├── chart   → 更新 msg.chart_type + 管线步骤                             │
  │       ├── complete → 标记所有步骤完成                                           │
  │       └── error   → 显示错误信息                                               │
  │                │                                                               │
  │                ▼                                                               │
  │  chatStore.ts:217  replaceMsgInArray(msg) → Vue 3 响应式渲染                   │
  │                │                                                               │
  │                ▼                                                               │
  │  ChatView.vue  → PipelineVisual (管线步骤) + ChartRenderer (图表)              │
  └────────────────┬────────────────────────────────────────────────────────────────┘
                   │
                   │  HTTP POST /chat-bi/api/v1/query/stream
                   │  Content-Type: application/json
                   │  Authorization: Bearer <JWT>
                   │
  ┌────────────────▼────────────────────────────────────────────────────────────────┐
  │  后端 (FastAPI)                                                                │
  │                                                                                │
  │  query.py:462  @router.post("/stream")                                         │
  │       │                                                                        │
  │       ├── query.py:59   _check_datasource() — 校验数据源存在+启用               │
  │       │                                                                        │
  │       ▼                                                                        │
  │  query.py:482  async def event_stream():                                       │
  │       │                                                                        │
  │       ├── query.py:487  cache_get() — 精确缓存检查                             │
  │       │       │  [cache_service.py:71]                                          │
  │       │       ├── 命中 → yield SSE cache 事件 → 走缓存命中路径                  │
  │       │       └── 未命中 → 继续                                                │
  │       │                                                                        │
  │       ├── query.py:613  semantic_cache_get() — 语义缓存检查                    │
  │       │       │  [cache_service.py:203]                                         │
  │       │       ├── 命中 → yield SSE cache 事件 → 走语义缓存路径                  │
  │       │       └── 未命中 → 继续                                                │
  │       │                                                                        │
  │       ▼  缓存未命中 — 调用管线执行器                                            │
  │                                                                                │
  │  query.py:727  async for event in execute_query_pipeline(...):                 │
  │       │  [pipeline_executor.py:8]                                              │
  │       │                                                                        │
  │       ▼                                                                        │
  │  ┌──────────────────────────────────────────────────────────────────────────┐  │
  │  │  pipeline_executor.py — 完整管线执行                                     │  │
  │  │                                                                          │  │
  │  │  Step 0: 缓存检查                                                        │  │
  │  │    pipeline_executor.py:25  cache_get()                                  │  │
  │  │    pipeline_executor.py:136 semantic_cache_get()                         │  │
  │  │    → yield {"event": "cache", ...}                                       │  │
  │  │                                                                          │  │
  │  │  Step 1: 意图识别                                                        │  │
  │  │    pipeline_executor.py:247  classify_intent(question)                   │  │
  │  │    │  [intent.py:158]                                                    │  │
  │  │    │  ├── Redis 缓存 → 命中直接返回                                      │  │
  │  │    │  ├── 关键词匹配 → _keyword_classify() [intent.py:87]               │  │
  │  │    │  └── LLM 调用 → ChatOpenAI.ainvoke() [intent.py:218]              │  │
  │  │    → yield {"event": "intent", ...}                                      │  │
  │  │                                                                          │  │
  │  │  Step 2: Schema 选择                                                     │  │
  │  │    pipeline_executor.py:260  schema_selection_node(state)                │  │
  │  │    │  [schema_selection.py]                                              │  │
  │  │    │  ├── _select_tables() — LLM 选表 (Step 1)                          │  │
  │  │    │  └── _select_columns() — LLM 选字段 (Step 2)                       │  │
  │  │    → yield {"event": "semantics", ...}                                   │  │
  │  │                                                                          │  │
  │  │  Step 3: SQL 生成                                                        │  │
  │  │    pipeline_executor.py:278  generate_sql(question, schema_context)      │  │
  │  │    │  [generation.py]                                                    │  │
  │  │    │  ├── attempt1: 精选 schema → LLM 生成 SQL                          │  │
  │  │    │  ├── attempt2: 完整 schema → LLM 重试                              │  │
  │  │    │  └── attempt3: 高温度 + 简化提示 → LLM 再试                        │  │
  │  │    │  + 表名校验 + 列名校验 (幻觉修复)                                   │  │
  │  │    → yield {"event": "sql", ...}                                         │  │
  │  │                                                                          │  │
  │  │  Step 4: SQL 执行                                                        │  │
  │  │    pipeline_executor.py:308  execute_sql(sql, datasource_id)             │  │
  │  │    │  [execution.py:102]                                                 │  │
  │  │    │  ├── validate_sql() — SQLGlot AST 校验 [execution.py:51]           │  │
  │  │    │  ├── pool_manager.get_pool() — 获取连接池 [connection_pool.py:47]  │  │
  │  │    │  ├── SET SESSION TRANSACTION READ ONLY — 数据库只读保护             │  │
  │  │    │  ├── asyncio.timeout(30s) — 超时保护                               │  │
  │  │    │  ├── conn.execute(text(sql)) — 执行 SQL                             │  │
  │  │    │  ├── 结果截断 (max_rows=1000)                                       │  │
  │  │    │  └── 类型序列化 (datetime/bytes → str)                              │  │
  │  │    → yield {"event": "data", ...}                                        │  │
  │  │                                                                          │  │
  │  │  Step 5: SQL 自愈 (失败时)                                               │  │
  │  │    pipeline_executor.py:323  self_heal_sql()                             │  │
  │  │    │  [self_heal.py:171]                                                 │  │
  │  │    │  ├── extract_error_code() — 提取 MySQL 错误码 [self_heal.py:73]   │  │
  │  │    │  ├── build_fix_prompt() — 构建修复 prompt [self_heal.py:100]      │  │
  │  │    │  ├── LLM 生成修复 SQL                                              │  │
  │  │    │  └── execute_sql() — 验证修复结果                                  │  │
  │  │    → yield {"event": "sql", ...} (修复后 SQL)                            │  │
  │  │    → yield {"event": "data", ...} (修复后执行结果)                       │  │
  │  │                                                                          │  │
  │  │  Step 6: 图表推断                                                        │  │
  │  │    pipeline_executor.py:346  infer_chart_type(columns, rows)             │  │
  │  │    │  [chart_type.py]                                                    │  │
  │  │    │  └── 纯规则推断: 列数×行数×数据类型 → chart_type                    │  │
  │  │    → yield {"event": "chart", ...}                                       │  │
  │  │                                                                          │  │
  │  │  完成                                                                    │  │
  │  │    → yield {"event": "complete", ...}                                    │  │
  │  └──────────────────────────────────────────────────────────────────────────┘  │
  │       │                                                                        │
  │       ▼  每个 yield 事件被 query.py 格式化为 SSE 文本                         │
  │  query.py:728  yield f"event: {event['event']}\ndata: {json}\n\n"             │
  │       │                                                                        │
  │       ▼                                                                        │
  │  query.py:784  StreamingResponse(event_stream(), media_type="text/event-stream")│
  │       │                                                                        │
  │       ▼  流结束后，执行审计和缓存写入                                           │
  │  query.py:748  log_action() — 审计日志 [audit_service.py:14]                  │
  │  query.py:759  _auto_save_history() — 保存查询历史                              │
  │  query.py:769  cache_set() — 缓存成功结果 [cache_service.py:87]               │
  │  query.py:780  db.commit() — 提交事务                                          │
  └─────────────────────────────────────────────────────────────────────────────────┘
                   │
                   │  SSE 文本流（HTTP 长连接）
                   │  Content-Type: text/event-stream
                   │  X-Accel-Buffering: no
                   │
                   ▼
  ┌─────────────────────────────────────────────────────────────────────────────────┐
  │  数据库层                                                                       │
  │                                                                                │
  │  MySQL (chatbi 库)                                                             │
  │    ├── audit_logs — 审计日志表                                                  │
  │    ├── analytics_events — 分析事件表                                            │
  │    ├── saved_queries — 查询历史表                                               │
  │    ├── data_sources — 数据源配置表                                               │
  │    ├── metadata_configs — 元数据配置表                                           │
  │    └── async_queries — 异步查询任务表                                            │
  │                                                                                │
  │  Redis                                                                         │
  │    ├── query:* — 精确缓存 (TTL 由 config 控制)                                  │
  │    ├── semantic:* — 语义缓存索引                                                │
  │    ├── cache_index:* — 数据源缓存索引 (用于批量清除)                             │
  │    ├── intent:* — 意图分类缓存 (TTL=300s)                                       │
  │    ├── rate_limit:* — 限流计数器                                                │
  │    ├── login_lock:* — 登录失败锁                                                │
  │    └── async_trace:* — 异步查询进度 (TTL=管线超时)                               │
  │                                                                                │
  │  用户数据源 (MySQL/PostgreSQL/SQLite)                                            │
  │    └── 连接池管理: ConnectionPoolManager._pools [connection_pool.py:45]         │
  └─────────────────────────────────────────────────────────────────────────────────┘
```

---

*本指南第 6-7 章及附录由 Claude Code 生成，最后更新: 2026-05-08*
