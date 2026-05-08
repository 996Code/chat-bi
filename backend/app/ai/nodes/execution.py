"""
SQL 执行节点 — LangGraph 流水线的"动手"环节
============================================

本文件是 LangGraph AI 查询流程中的**执行节点**，负责把 LLM 生成的 SQL 真正送到数据库运行。

## 在 LangGraph 流水线中的位置

    用户提问 → 意图识别 → 上下文补全 → Schema选择 → SQL生成 → 【本文件: 执行SQL】 → 自愈(失败时) → 图表推断 → 返回结果

## 核心职责

1. **安全校验** — 用 SQLGlot 解析 SQL 的 AST（抽象语法树），只放行 SELECT 语句
2. **只读保护** — 在数据库连接上设置 READ ONLY 事务，从数据库层面杜绝写操作
3. **超时控制** — asyncio.timeout 限制查询执行时间，防止慢查询卡死服务
4. **类型序列化** — 把 MySQL 特有类型（datetime、bytes）转成 JSON 可序列化的格式

## 与其他文件的关系

- `graph.py` — 在 execution_node 中调用本文件的 execute_sql()
- `self_heal.py` — 执行失败时，自愈节点也会调用 execute_sql() 重新执行修复后的 SQL
- `connection_pool.py` — 提供数据库连接池管理，本文件通过 pool_manager 获取引擎
- `config.py` — sql_execution_timeout(30秒)、query_max_rows(1000条) 等安全阈值

## 关键 Python 概念

- asyncio: Python 异步编程框架，用 async/await 实现非阻塞 I/O
- context manager: async with 语句，确保资源（数据库连接）自动释放
- AST: Abstract Syntax Tree（抽象语法树），SQLGlot 把 SQL 文本解析成结构化对象
"""

import asyncio  # Python 标准库：异步 I/O 框架，提供 async/await、timeout 等功能
import datetime  # Python 标准库：日期时间类型，用于类型检查和序列化
import re  # Python 标准库：正则表达式，用于检测危险 SQL 关键字
import time  # Python 标准库：monotonic 时钟，用于精确计时（不受系统时间调整影响）
from typing import Any  # 类型注解：Any 表示任意类型

import sqlglot  # 第三方库：SQL 解析器，能把 SQL 文本解析成 AST（抽象语法树）
from sqlalchemy import select, text  # SQLAlchemy：select 构建查询，text 包装原始 SQL
from sqlglot import ParseError  # SQLGlot 解析失败时抛出的异常

from app.core.config import settings  # 项目配置中心，所有魔法数字的来源
from app.core.logging import get_logger  # 统一日志工具
from app.db.session import get_db  # 异步数据库会话生成器（yield 方式）
from app.db.models import DataSource  # 数据源 ORM 模型
from app.services.connection_pool import pool_manager  # 连接池管理器（单例）

logger = get_logger(__name__)  # __name__ 即 "app.ai.nodes.execution"，日志中可追溯来源


def validate_sql(sql: str, dialect: str = "mysql") -> tuple[bool, str]:
    """使用 SQLGlot AST 验证 SQL，拒绝非 SELECT 语句。

    这是 SQL 执行前的**第一道安全防线**，在 SQL 真正送到数据库之前就拦截危险操作。

    验证分两层：
    1. AST 层：用 SQLGlot 把 SQL 解析成抽象语法树，检查根节点是否为 Select
       — 这比正则匹配更可靠，因为能理解 SQL 结构（不会被注释、子查询绕过）
    2. 关键字层：正则扫描 DROP/DELETE 等危险关键字
       — 双重保险：防止在子查询、CTE 等位置隐藏写操作

    参数:
        sql: 待验证的 SQL 语句
        dialect: SQL 方言（默认 mysql），SQLGlot 据此选择对应的语法规则解析

    返回:
        tuple[bool, str] — (是否安全, 错误信息)
        - 安全时返回 (True, "")
        - 不安全时返回 (False, "错误原因")

    Python 知识:
        - tuple[bool, str] 是 Python 3.9+ 的类型注解写法
        - 函数返回多个值时，Python 自动打包成 tuple，调用方可用 valid, error = validate_sql(...) 解包
    """
    try:
        # sqlglot.parse_one() 把 SQL 文本解析成 AST 对象
        # 如果 SQL 语法有误，会抛出 ParseError
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as e:
        return False, f"SQL 语法错误: {e}"

    # 核心安全检查：AST 的根节点必须是 Select 类型
    # isinstance() 检查对象是否属于某个类（或其子类）
    # 如果用户写了 "DROP TABLE users"，parse_one 返回的是 Drop 类型，不是 Select
    if not isinstance(parsed, sqlglot.exp.Select):
        return False, "仅支持 SELECT 查询"

    # 第二层防线：正则扫描危险关键字
    # 即使 AST 根节点是 SELECT，子查询或 CTE 中也可能包含写操作
    # 例如: SELECT * FROM (DELETE FROM users) AS t — 虽然 SELECT 外壳，但内含 DELETE
    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE"]
    # \b 是单词边界，防止误判（如 "UPDATED_AT" 中的 UPDATE）
    # re.IGNORECASE 让匹配不区分大小写（drop 和 DROP 都能匹配）
    pattern = re.compile(r'\b(' + '|'.join(dangerous_keywords) + r')\b', re.IGNORECASE)
    match = pattern.search(sql)
    if match:
        return False, f"禁止使用 {match.group(1)} 语句"

    return True, ""


async def execute_sql(sql: str, datasource_id: str, dialect: str = "mysql", tenant_id: str | None = None) -> dict[str, Any]:
    """执行 SQL 并返回结果。带 30 秒超时保护。

    这是 LangGraph 执行节点的核心函数，被 graph.py 和 self_heal.py 调用。

    执行流程:
        1. validate_sql() — AST 安全校验（第一道防线）
        2. 获取数据库引擎 — 先查连接池缓存，没有则从 DB 加载数据源配置
        3. SET READ ONLY — 数据库层面的只读保护（第二道防线）
        4. 执行 SQL — 带 asyncio.timeout 超时控制（第三道防线）
        5. 结果截断 — 超过 query_max_rows(默认1000) 时截断
        6. 类型序列化 — datetime/bytes 转 JSON 安全类型

    参数:
        sql: 要执行的 SQL 语句（已由 LLM 生成）
        datasource_id: 数据源 ID，决定连哪个数据库
        dialect: SQL 方言，默认 mysql（SQLGlot 解析用）
        tenant_id: 租户 ID，用于多租户数据隔离校验

    返回:
        成功: {"success": True, "columns": [...], "rows": [...], "row_count": N, ...}
        失败: {"success": False, "error": "错误原因"}

    Python 知识:
        - async def 定义协程函数，调用时必须用 await
        - str | None 是 Python 3.10+ 的联合类型写法，等价于 Optional[str]
        - dict[str, Any] 表示键为字符串、值为任意类型的字典
    """

    # ── 第一道防线：AST 安全校验 ──
    valid, error = validate_sql(sql, dialect)
    if not valid:
        return {"success": False, "error": error}

    try:
        # ── 获取数据库引擎（连接池） ──
        # pool_manager 维护了一个 {datasource_id: AsyncEngine} 的字典缓存
        # 优先从缓存取，避免每次执行都重新创建连接（创建连接开销很大）
        engine = await pool_manager.get_pool_by_id(datasource_id)
        if not engine:
            # 缓存未命中：从数据库查询数据源配置，然后创建连接池
            # get_db() 是一个异步生成器，用 async for 消费，用完自动关闭会话
            # 这种 "async for + break" 模式是从异步生成器获取单个值的惯用写法
            async for db in get_db():
                query = select(DataSource).where(DataSource.id == datasource_id)
                # 多租户隔离：如果提供了 tenant_id，额外校验数据源归属
                # 防止 A 租户通过篡改 datasource_id 访问 B 租户的数据源
                if tenant_id:
                    query = query.where(DataSource.tenant_id == tenant_id)
                result = await db.execute(query)
                ds = result.scalar_one_or_none()  # 取单条记录，不存在返回 None
                if not ds:
                    return {"success": False, "error": "数据源不存在"}
                if not ds.is_active:
                    return {"success": False, "error": "数据源已禁用"}
                # 根据数据源配置（host/port/用户名/密码）创建连接池并缓存
                engine = await pool_manager.get_pool(ds)
                break
            if not engine:
                return {"success": False, "error": "数据源连接池未初始化"}

        # time.monotonic() 返回单调递增时钟，不受系统时间修改影响
        # 比 time.time() 更适合用于测量时间间隔
        start = time.monotonic()

        # ── 第二道 + 第三道防线：只读保护 + 超时控制 ──
        # asyncio.timeout() 是 Python 3.11+ 引入的异步超时上下文管理器
        # 超过 settings.sql_execution_timeout（默认30秒）自动抛出 TimeoutError
        # 注意：它会在超时时自动取消正在执行的协程，防止资源泄漏
        async with asyncio.timeout(settings.sql_execution_timeout):
            # engine.connect() 获取一个数据库连接
            # async with 确保连接在使用后自动归还到连接池（即使发生异常）
            async with engine.connect() as conn:
                try:
                    # ── 第二道防线：数据库层面只读保护 ──
                    # SET SESSION TRANSACTION READ ONLY 是 MySQL 8.0+ 的语法
                    # 开启后，该连接上的所有写操作（INSERT/UPDATE/DELETE 等）都会被数据库拒绝
                    # 这是比应用层校验更安全的保护——即使 SQL 绕过了 validate_sql()，数据库也会拦住
                    await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                except Exception:
                    # 兼容低版本 MySQL：5.7 及以下不支持上面那条语法
                    # 用 SET default_transaction_read_only = on 作为降级方案
                    await conn.execute(text("SET default_transaction_read_only = on"))
                # text() 把原始 SQL 字符串包装成 SQLAlchemy 可执行对象
                # 这里才是真正执行用户 SQL 的地方
                result = await conn.execute(text(sql))
                # result.keys() 返回列名列表，如 ["id", "name", "age"]
                columns = list(result.keys())
                # result.fetchall() 取回所有行
                # row._mapping 是 SQLAlchemy 的 RowMapping 对象，类似字典
                # 用 dict() 转成普通字典，方便后续 JSON 序列化
                rows = [dict(row._mapping) for row in result.fetchall()]

        # 计算执行耗时（毫秒），用于前端展示和性能监控
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # ── 结果截断：防止返回过多数据导致内存溢出或前端卡顿 ──
        truncated = False
        if len(rows) > settings.query_max_rows:
            rows = rows[:settings.query_max_rows]  # 列表切片，只保留前 N 条
            truncated = True  # 标记已截断，前端会提示"结果已截断"

        # ── 类型序列化：把 MySQL 特有类型转成 JSON 可序列化的类型 ──
        # JSON 标准只支持 string/number/boolean/null/array/object
        # Python 的 datetime 和 bytes 无法直接序列化为 JSON，必须先转换
        for row in rows:
            for k, v in row.items():
                if isinstance(v, (datetime.datetime, datetime.date)):
                    # datetime → 字符串，如 "2024-01-15 10:30:00"
                    # isinstance 第二个参数用元组，表示"属于其中任一类型"
                    row[k] = str(v)
                elif isinstance(v, bytes):
                    # bytes → 字符串，用 UTF-8 解码
                    # errors="replace" 遇到无法解码的字节用 � 替代，不会抛异常
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
        # 查询超时：记录日志并返回友好错误信息
        # sql[:200] 只截取 SQL 前 200 字符，防止超长 SQL 塞满日志
        logger.error("SQL execution timed out after %ds: %s", settings.sql_execution_timeout, sql[:200])
        return {"success": False, "error": f"查询超时（{settings.sql_execution_timeout}秒限制）"}
    except Exception as e:
        # 兜底异常处理：捕获所有未预期的错误
        # 在生产环境中，绝不能让异常冒泡到 LangGraph 层导致整个流程崩溃
        logger.error("SQL execution failed: %s — %s", sql[:200], e)
        return {"success": False, "error": f"SQL 执行失败: {e}"}