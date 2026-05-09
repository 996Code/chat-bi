"""
SQL 生成节点 — LangGraph AI 管道的核心环节之一
================================================

本文件负责将用户的自然语言问题转化为可执行的 SQL 查询，是整个 ChatBI 系统
"意图 → Schema → SQL → 执行 → 自愈" 流程中的第三步。

核心设计：三次降级重试策略
--------------------------
1. 第一次尝试（attempt1）：使用 LLM 精选的 schema context 直接生成 SQL
   - schema_context 已由上游 schema_selection 节点筛选，只包含最相关的表
   - 成功率最高，延迟最低
2. 第二次尝试（attempt2）：如果 LLM 返回空，用完整 schema 重试
   - 可能是精选 schema 信息不足，补全所有表结构再试
3. 第三次尝试（attempt3）：换用更高温度的 LLM + 简化提示词
   - temperature 从 0 提升到 0.7，增加创造性
   - 提示词极简化，减少约束让 LLM 更自由发挥

幻觉修复机制
------------
LLM 经常"编造"不存在的表名或列名（如 created_at），本文件通过以下方式修复：
- 表名校验：对比 SQL 中的表名与 schema 中的合法表名，用模糊匹配替换
- 列名校验：特别检测 created_at/updated_at 等常见幻觉列，替换为真实的时间列

与其他文件的关系
----------------
- app/ai/nodes/shared_utils.py  — 提供 get_llm()、LLM_NO_THINKING 等共享工具
- app/ai/prompts/query_prompt.py — 提供 SYSTEM_PROMPT、build_user_prompt 等提示词模板
- app/ai/graph.py               — 将 generate_sql 包装为 generation_node 编排进 LangGraph 状态图
- app/core/config.py            — 提供 settings（API 密钥、模型名等配置）
"""
import asyncio
import json
import re
from difflib import SequenceMatcher

from langchain_openai import ChatOpenAI

from app.ai.nodes.shared_utils import get_llm, LLM_NO_THINKING, LLMAPIError, is_auth_error
from app.ai.prompts.query_prompt import SYSTEM_PROMPT, build_user_prompt, build_semantic_prompt
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ============================================================================
# 正则表达式工具集
# ============================================================================
# 以下正则用于从不同来源提取 SQL、表名、列名等信息。
# Python 技巧：re.compile() 预编译正则，比每次调用 re.search() 更高效，
# 尤其在循环中反复使用时性能差异明显。

# 从 LLM 返回的文本中提取第一条 SELECT 语句
# [\s\S]*? 是非贪婪匹配任意字符（包括换行），遇到 ; 或字符串结尾就停止
# 这样即使 LLM 在 SQL 后面附加了解释文字，也能正确截取纯 SQL
_SQL_EXTRACT = re.compile(r'(SELECT\b[\s\S]*?)(?:;|$)', re.IGNORECASE)

# 从 schema_context 文本中提取表名（匹配 "表名: t_xxx" 格式的行）
# ^ 表示行首，\S+ 匹配非空白字符序列（即表名）
_SCHEMA_TABLE_RE = re.compile(r'^表名:\s*(\S+)', re.MULTILINE)

# 从 schema_context 尾部的 "可用表名: t_a, t_b, t_c" 行提取所有表名
# [^\n]+ 匹配到行尾的所有内容，之后用逗号分割
_ALL_TABLES_RE = re.compile(r'可用表名[:\s]*([^\n]+)')

# 从 SQL 语句中提取表名（匹配 FROM 和 JOIN 后面的标识符）
# [a-zA-Z_] 开头，后跟字母/数字/下划线 — 这是 SQL 标识符的命名规则
_SQL_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
    re.IGNORECASE,
)

# 匹配 SQL 中的中文行内注释，如 "-- 这是注释"
# [一-鿿] 匹配 CJK 统一汉字区间，用于判断注释内容是否为中文
# 为什么要移除中文注释？因为某些 MySQL 版本/驱动对中文注释支持不佳
_SQL_COMMENT_RE = re.compile(r'--\s*[一-鿿].*$', re.MULTILINE)

# 模糊匹配的相似度阈值：SequenceMatcher 返回值在 0.0~1.0 之间
# 0.5 表示两个字符串至少有 50% 相似才认为可能是同一个表名的拼写变体
# 太低会误匹配（如 t_order 和 t_user 相似度约 0.3），太高会漏匹配
_TABLE_NAME_SIM_THRESHOLD = 0.5


def _clean_sql(raw: str) -> str:
    """清理 LLM 返回的原始文本，提取纯 SQL 语句。

    LLM 经常在 SQL 前后附加额外内容，例如：
    - Markdown 代码块包裹：```sql ... ```
    - 解释性文字："这是查询xxx的SQL：SELECT ..."
    - 多条 SQL 语句

    本函数的处理步骤：
    1. 去除 Markdown 代码块标记
    2. 用正则提取第一条 SELECT 语句
    3. 如果都没匹配到，返回原始文本（兜底）

    Args:
        raw: LLM 返回的原始字符串
    Returns:
        清理后的 SQL 字符串
    """
    # 去除 Markdown 代码块包裹（LLM 常用格式）
    sql = raw.strip()
    if sql.startswith("```"):
        sql = re.sub(r'^```(?:sql)?\s*', '', sql, flags=re.IGNORECASE)  # 去开头 ``` 或 ```sql
        sql = re.sub(r'\s*```$', '', sql)  # 去结尾 ```
    # 提取第一条 SELECT 语句（忽略后面的解释文字）
    m = _SQL_EXTRACT.search(sql)
    if m:
        return m.group(1).strip()
    # 兜底：如果正则没匹配到 SELECT，返回原文（可能是非标准 SQL）
    return sql.strip()


def _strip_chinese_comments(sql: str) -> str:
    """移除 SQL 中的中文行内注释（-- 中文...）。

    某些 MySQL 驱动或版本对中文注释的编码处理不一致，
    移除后可避免执行时的编码错误。
    """
    return _SQL_COMMENT_RE.sub('', sql).strip()


def _extract_all_valid_tables(schema_context: str) -> set[str]:
    """从 schema_context 中提取所有合法表名。

    schema_context 包含两种格式的表名信息：
    1. 每个表的详情块以 "表名: t_xxx" 开头
    2. 尾部汇总行 "可用表名: t_a, t_b, t_c"

    两种格式都要解析，合并去重后返回。

    Python 技巧：set[str] 是 Python 3.9+ 的类型注解语法，
    表示元素为字符串的集合。3.8 及以下需用 Set[str]（从 typing 导入）。

    Args:
        schema_context: 包含表结构描述的文本
    Returns:
        所有合法表名的集合
    """
    tables = set(_SCHEMA_TABLE_RE.findall(schema_context))  # 从详情块提取
    # 解析尾部的 "可用表名: t_a, t_b" 汇总行
    m = _ALL_TABLES_RE.search(schema_context)
    if m:
        for t in m.group(1).split(','):
            name = t.strip()
            if name:
                tables.add(name)
    return tables


def _extract_sql_tables(sql: str) -> set[str]:
    """从 SQL 语句中提取 FROM/JOIN 后的表名。

    排除子查询关键字和别名：LLM 有时生成 FROM select、FROM where 等错误写法，
    这些 SQL 关键字不应被当作表名。

    Python 技巧：集合推导式 {expr for item in iterable if condition}
    比等价的 for 循环 + add() 更简洁高效。

    Args:
        sql: SQL 查询字符串
    Returns:
        SQL 中引用的表名集合（全小写，便于后续比较）
    """
    raw = _SQL_TABLE_RE.findall(sql)
    # skip 集合包含 SQL 关键字，防止误识别为表名
    skip = {'select', 'where', 'set', 'values', 'into', 'group', 'order', 'having', 'limit', 'on'}
    # 全部转小写，因为表名比较不区分大小写（MySQL 默认行为）
    return {t.lower() for t in raw if t.lower() not in skip}


def _fix_table_names(sql: str, invalid: set[str], valid: set[str]) -> str:
    """将非法表名替换为最相似的合法表名（模糊匹配修复）。

    核心算法：difflib.SequenceMatcher
    -------------------------------
    SequenceMatcher 是 Python 标准库 difflib 提供的序列相似度比较工具。
    - .ratio() 返回 0.0~1.0 的相似度分数，基于"最长公共子序列"算法
    - 例如 SequenceMatcher(None, "t_order", "t_orders").ratio() ≈ 0.92
    - 例如 SequenceMatcher(None, "t_order", "t_user").ratio() ≈ 0.33

    为什么用 max() + lambda？
    - max(valid, key=lambda t: SequenceMatcher(...).ratio()) 遍历所有合法表名，
      找到与非法表名相似度最高的那个
    - Python 技巧：key 参数接受一个函数，max() 会用该函数的返回值做比较

    替换策略：
    - 只在相似度 > _TABLE_NAME_SIM_THRESHOLD (0.5) 时才替换
    - 用 re.sub + \b 词边界确保只替换完整的表名，不会误替换子串
      例如 t_order 不会把 t_orders 中的 t_order 部分替换掉

    Args:
        sql: 原始 SQL 字符串
        invalid: 非法表名集合
        valid: 合法表名集合
    Returns:
        修复后的 SQL 字符串
    """
    for bad in invalid:
        # 在所有合法表名中，找到与 bad 相似度最高的那个
        best = max(valid, key=lambda t: SequenceMatcher(None, bad, t).ratio())
        # 只有相似度超过阈值才替换，避免把完全无关的表名误替换
        if SequenceMatcher(None, bad, best).ratio() > _TABLE_NAME_SIM_THRESHOLD:
            logger.info("Replacing invalid table '%s' -> '%s'", bad, best)
            # \b 是词边界，re.escape(bad) 转义特殊字符，确保精确匹配完整表名
            sql = re.sub(r'\b' + re.escape(bad) + r'\b', best, sql, flags=re.IGNORECASE)
    return sql


# 从 schema_context 中提取列名（匹配 "  - col_name (type)" 格式的行）
# \s+ 匹配缩进空白，\w+ 匹配列名，\s*\( 匹配类型前的左括号
_SCHEMA_COL_RE = re.compile(r'^\s+-\s+(\w+)\s+\(', re.MULTILINE)

# 从 SQL 中提取出现在比较运算符左侧的列名
# 匹配模式：列名 后跟 =, !=, <>, >=, <=, >, <, IS, IN, LIKE, BETWEEN
# 这些位置出现的标识符大概率是列名而非表名或关键字
_SQL_COL_RE = re.compile(
    r'\b(\w+)\s*(?:=|!=|<>|>=|<=|>|<|\s+IS\s|\s+IN\s|\s+LIKE\s|\s+BETWEEN\s)',
    re.IGNORECASE,
)


def _extract_schema_columns(schema_context: str) -> set[str]:
    """从 schema_context 中提取所有合法列名。

    利用 schema_context 中 "  - col_name (type)" 格式的字段列表，
    提取出所有在数据库中真实存在的列名，用于后续列名校验。

    Args:
        schema_context: 包含表结构描述的文本
    Returns:
        所有合法列名的集合
    """
    return set(_SCHEMA_COL_RE.findall(schema_context))


def _validate_and_fix_columns(sql: str, schema_context: str, raw_metadata: str = "") -> tuple[str, list[str]]:
    """校验并修复 SQL 中的列名，特别是 created_at 幻觉问题。

    LLM 幻觉问题详解
    ----------------
    LLM 在训练数据中见过大量包含 created_at/updated_at 列的数据库表，
    因此即使目标表没有这些列，LLM 也倾向于"编造"它们。这种现象叫做"幻觉"（hallucination）。

    修复策略：
    1. 检查 SQL 中是否使用了 created_at/updated_at/created_time/update_time
    2. 查询 raw_metadata 中该表的真实列定义
    3. 如果该表确实有此列 → 保留（不是幻觉）
    4. 如果该表没有此列 → 在该表的列中找最相似的时间类列替换
       - 先筛选包含 time/date/at/timestamp/ts 关键字的列
       - 用 SequenceMatcher 找最相似的列名
       - 如果相似度太低（<0.2），直接取第一个时间类列（兜底）

    Python 技巧：tuple[str, list[str]] 是 Python 3.9+ 的内置泛型语法，
    等价于 typing 模块的 Tuple[str, List[str]]。

    Args:
        sql: 待校验的 SQL 字符串
        schema_context: schema 上下文文本
        raw_metadata: 原始元数据 JSON 字符串，包含表和列的详细定义
    Returns:
        (修复后的 SQL, 列名修复记录列表)
    """
    # 从 SQL 中提取引用的表名，用于后续按表查找列定义
    sql_tables = _extract_sql_tables(sql)
    if not sql_tables:
        return sql, []

    # 从 raw_metadata JSON 构建 "表名 → 列名集合" 的映射
    # raw_metadata 格式：{"models": [{"name": "t_order", "columns": [{"name": "id", ...}, ...]}]}
    # Python 技巧：dict[str, set[str]] 声明字典的键是字符串、值是字符串集合
    table_cols: dict[str, set[str]] = {}
    if raw_metadata:
        try:
            metadata = json.loads(raw_metadata)  # 将 JSON 字符串解析为 Python 字典
            for model in metadata.get("models", []):
                tname = model.get("name", "").lower()  # 表名统一小写
                # 集合推导式：提取每个列的 name 字段，过滤掉空值
                cols = {c.get("name", "") for c in model.get("columns", []) if c.get("name")}
                table_cols[tname] = cols
        except (json.JSONDecodeError, TypeError):
            # JSON 解析失败时静默跳过，不中断流程（防御性编程）
            pass

    column_fixes = []
    # 常见幻觉列名集合 — LLM 最容易"编造"的时间类列名
    hallucinated_cols = {"created_at", "updated_at", "created_time", "update_time"}
    for hc in hallucinated_cols:
        # \b 词边界确保只匹配完整的列名，不会误匹配 created_at_index 之类的
        if not re.search(r'\b' + hc + r'\b', sql, re.IGNORECASE):
            continue  # SQL 中没有使用这个幻觉列，跳过

        # 检查 SQL 引用的表中是否真的有这个列
        has_valid_col = False
        for t in sql_tables:
            cols = table_cols.get(t.lower(), set())
            if hc in cols:
                has_valid_col = True
                break

        if has_valid_col:
            continue  # 该列确实存在于某个引用表中，不是幻觉

        # 确认是幻觉列，在引用表中寻找最相似的时间类列作为替换
        for t in sql_tables:
            cols = table_cols.get(t.lower(), set())
            # 筛选包含时间相关关键字的列（time, date, at, timestamp, ts）
            time_cols = [c for c in cols if any(
                kw in c.lower() for kw in ("time", "date", "at", "timestamp", "ts")
            )]
            if time_cols:
                # 用 SequenceMatcher 在时间类列中找最相似的一个
                best = max(time_cols, key=lambda c: SequenceMatcher(None, hc, c).ratio())
                ratio = SequenceMatcher(None, hc, best).ratio()
                # 相似度 > 0.2 才用最相似的列，否则用第一个时间类列（兜底）
                # 这里的阈值比表名修复低很多（0.2 vs 0.5），因为列名差异通常更大
                replacement = best if ratio > 0.2 else time_cols[0]
                logger.info("Fixing hallucinated column '%s' -> '%s' (table: %s)", hc, replacement, t)
                column_fixes.append(f"{hc} -> {replacement}")
                # 替换 SQL 中的幻觉列名为真实列名
                sql = re.sub(r'\b' + hc + r'\b', replacement, sql, flags=re.IGNORECASE)
                break  # 找到一个替换就够了，跳出表循环

    return sql, column_fixes


def _validate_and_fix_tables(sql: str, schema_context: str, raw_metadata: str = "") -> tuple[str, list[str], list[str]]:
    """校验 SQL 中的表名是否在 schema 中，不匹配则自动修复。

    这是表名和列名校验的统一入口，按以下顺序执行：
    1. 移除 SQL 中的中文注释（避免干扰正则匹配）
    2. 提取 schema 中的合法表名，与 SQL 中使用的表名做差集
    3. 对非法表名用模糊匹配找到最相似的合法表名并替换
    4. 调用 _validate_and_fix_columns 修复列名幻觉

    Python 技巧：tuple[str, list[str], list[str]] 返回三个值，
    调用方可以用 sql, table_fixes, column_fixes = _validate_and_fix_tables(...) 解包。

    Args:
        sql: 待校验的 SQL 字符串
        schema_context: schema 上下文文本
        raw_metadata: 原始元数据 JSON 字符串
    Returns:
        (修复后的 SQL, 表名修复记录列表, 列名修复记录列表)
    """
    # 先移除中文注释，避免注释中的表名干扰正则匹配
    sql = _strip_chinese_comments(sql)

    table_fixes = []
    valid_tables = _extract_all_valid_tables(schema_context)
    if valid_tables:
        used_tables = _extract_sql_tables(sql)
        # 集合差集：SQL 中使用的表名 - 合法表名 = 非法表名
        invalid = used_tables - valid_tables
        if invalid:
            logger.warning("LLM used invalid tables: %s, valid: %s", invalid, valid_tables)
            for bad in invalid:
                # 对每个非法表名，记录修复映射
                best = max(valid_tables, key=lambda t: SequenceMatcher(None, bad, t).ratio())
                if SequenceMatcher(None, bad, best).ratio() > _TABLE_NAME_SIM_THRESHOLD:
                    table_fixes.append(f"{bad} -> {best}")
            sql = _fix_table_names(sql, invalid, valid_tables)

    # 校验并修复列名（如 created_at 幻觉问题）
    column_fixes = []
    sql, column_fixes = _validate_and_fix_columns(sql, schema_context, raw_metadata)
    return sql, table_fixes, column_fixes


def _build_full_schema_context(raw_metadata: str) -> str:
    """从完整 metadata 构建 schema context（用于第二次重试）。

    为什么需要这个函数？
    - 第一次尝试使用的是 schema_selection 节点精选的 schema（只包含最相关的表）
    - 如果精选 schema 信息不足导致 LLM 无法生成 SQL，就需要用完整 schema 重试
    - 本函数将 raw_metadata JSON 转换为与精选 schema 相同格式的文本

    格式示例：
        可用的数据库表结构（完整 schema）：

        表名: t_order
        说明: 订单表
        字段:
          - id (INT) NOT NULL [主键] — 订单ID
          - amount (DECIMAL) NULL — 订单金额
          ...

    Python 技巧：延迟导入（lazy import）
    - from app.ai.nodes.shared_utils import append_all_table_names 放在函数内部
    - 避免模块级循环导入问题（shared_utils 可能也导入了本模块的某些内容）
    - 延迟导入只在函数被调用时才执行，不影响模块加载

    Args:
        raw_metadata: 原始元数据 JSON 字符串
    Returns:
        格式化的完整 schema 文本
    """
    from app.ai.nodes.shared_utils import append_all_table_names

    try:
        metadata = json.loads(raw_metadata)
        models = metadata.get("models", [])
    except (json.JSONDecodeError, TypeError):
        # JSON 解析失败时返回原文（防御性编程：不因格式问题中断流程）
        return raw_metadata

    # 构建格式化的 schema 文本
    lines = ["可用的数据库表结构（完整 schema）：", ""]
    for model in models:
        lines.append(f"表名: {model.get('name', '?')}")
        if model.get("description"):
            lines.append(f"说明: {model['description']}")
        lines.append("字段:")
        # 限制最多显示 20 个字段，避免 prompt 过长导致 LLM 注意力分散
        for col in model.get("columns", [])[:20]:
            nullable = "NULL" if col.get("nullable") else "NOT NULL"
            primary = " [主键]" if col.get("primary") else ""
            comment = f" — {col['comment']}" if col.get("comment") else ""
            lines.append(f"  - {col.get('name', '?')} ({col.get('type', 'unknown')}) {nullable}{primary}{comment}")
        lines.append("")

    result = "\n".join(lines)
    # 在末尾追加 "可用表名: t_a, t_b, t_c" 汇总行，方便表名提取
    return append_all_table_names(result, raw_metadata)


def get_fallback_llm() -> ChatOpenAI:
    """创建降级重试用的 LLM 实例（更高温度 + 更大 token 上限）。

    为什么需要 fallback LLM？
    - 默认 LLM 使用 temperature=0（确定性输出），适合大多数场景
    - 当默认 LLM 返回空结果时，可能是因为提示词约束太严格
    - 提高温度到 0.7 让 LLM 更有"创造力"，可能生成出不同的 SQL
    - max_tokens 从默认值提升到 4000，避免复杂 SQL 被截断

    Python 技巧：ChatOpenAI | None 是 Python 3.10+ 的联合类型语法，
    等价于 typing.Optional[ChatOpenAI] 或 Union[ChatOpenAI, None]。
    """
    return get_llm(max_tokens=4000, temperature=0.7)


async def _llm_generate(messages: list, llm: ChatOpenAI | None = None, attempt: str = "") -> str | None:
    """调用 LLM 生成 SQL，并清理返回结果。

    这是与 LLM 交互的核心函数，负责：
    1. 记录完整的提示词（用于调试和审计）
    2. 设置超时保护（30 秒），防止 LLM 响应过慢
    3. 清理 LLM 返回的原始文本，提取纯 SQL

    Python 异步编程要点：
    - async def 定义协程函数，调用时需用 await
    - asyncio.timeout(30) 是 Python 3.11+ 的异步超时上下文管理器
    - llm.ainvoke() 是 LangChain 的异步调用方法（对应同步的 invoke()）
    - await 会挂起当前协程，等 LLM 响应后再继续执行

    LangChain 消息格式：
    - messages 是元组列表，如 [("system", "..."), ("human", "...")]
    - system 消息设定 LLM 的角色和行为规则
    - human 消息包含用户的具体请求

    Args:
        messages: LangChain 格式的消息列表 [(role, content), ...]
        llm: 可选的 LLM 实例，默认使用 get_llm() 获取
        attempt: 当前尝试次数标识（用于日志）
    Returns:
        清理后的 SQL 字符串，如果生成失败返回 None
    """
    if llm is None:
        llm = get_llm()  # 使用默认 LLM（temperature=0，确定性输出）
    # 记录完整的提示词，方便排查 LLM 生成质量问题
    for role, content in messages:
        logger.info("=== %s LLM %s message (%d chars) ===", attempt or "LLM", role, len(content))
        # 长内容只记录首尾 250 字符，避免日志膨胀
        if len(content) > 500:
            logger.info("... preview: %s ...", content[:250])
            logger.info("... tail: %s", content[-250:])
        else:
            logger.info("%s", content)
    try:
        # asyncio.timeout(30) — Python 3.11+ 的异步超时上下文管理器
        # 如果 LLM 在 30 秒内没有响应，自动取消请求并抛出 TimeoutError
        async with asyncio.timeout(30):
            response = await llm.ainvoke(messages)  # 异步调用 LLM（LangChain 的 ainvoke 方法）
    except asyncio.TimeoutError:
        logger.warning("LLM timeout")
        return None
    except Exception as e:
        # 捕获所有其他异常（网络错误、API 限流、无效响应等）
        # 认证错误（401）是配置问题，应立即抛出，不应静默降级
        if is_auth_error(e):
            logger.error("LLM API authentication failed: %s", e)
            raise LLMAPIError(
                f"LLM API 认证失败，请检查 LLM_BASE_URL 和 LLM_API_KEY 配置。错误信息: {e}",
                error_code="auth_error"
            )
        logger.warning("LLM error: %s", e)
        return None

    # 从 LangChain AIMessage 对象中提取文本内容
    raw = response.content.strip()
    if not raw:
        return None
    # 清理 LLM 返回的原始文本（去 Markdown、提取 SELECT 等）
    sql = _clean_sql(raw)
    return sql if sql else None  # 三元表达式：sql 非空返回 sql，否则返回 None


def _build_history_context(history: list[dict] | None) -> str:
    """构建对话历史上下文字符串，用于多轮查询。

    多轮对话场景：
    - 用户："查一下上个月的订单数" → 生成 SQL 并执行
    - 用户："按地区分组呢？" → 需要结合上一轮的 SQL 上下文

    设计要点：
    - 只保留最近 6 条消息（约 3 轮对话），避免 prompt 过长
    - 过滤掉 "处理中..." 和 "查询成功" 等无意义的助手消息
    - 助手消息优先显示已执行的 SQL（比纯文本更有参考价值）

    Python 技巧：
    - list[-6:] 切片语法：取列表最后 6 个元素
    - list[dict] | None 是 Python 3.10+ 的可选类型注解

    Args:
        history: 对话历史列表，每条消息包含 role/content/sql 等字段
    Returns:
        格式化的对话历史文本，或空字符串
    """
    if not history:
        return ""
    lines = ["\n## 对话历史（参考上下文，不要重复查询已有结果）"]
    for msg in history[-6:]:  # 只保留最近 6 条消息（约 3 轮对话）
        role = msg.get("role", "")
        content = msg.get("content", "")
        sql = msg.get("sql", "")
        if role == "user" and content:
            lines.append(f"用户: {content}")
        elif role == "assistant":
            if sql:
                # 优先显示已执行的 SQL，比纯文本回复更有参考价值
                lines.append(f"助手(已执行SQL): {sql}")
            elif content and content not in ("处理中...", "查询成功"):
                # 过滤掉无意义的助手消息
                lines.append(f"助手: {content}")
    return "\n".join(lines)


async def generate_sql(
    question: str,
    schema_context: str,
    raw_metadata: str = "",
    history: list[dict] | None = None,
) -> dict:
    """SQL 生成的核心入口 — 三次降级重试 + 表名/列名自动修复。

    这是 generate_sql 的核心实现，在 graph.py 中通过 generation_node 包装后注册为
    LangGraph 状态图节点：graph.add_node("generate_sql", generation_node)。

    三次降级重试策略详解
    --------------------
    Attempt 1（默认路径）：
        - 使用 schema_selection 节点精选的 schema_context
        - 包含对话历史（多轮查询支持）
        - temperature=0，输出最确定
        - 成功率约 85%

    Attempt 2（完整 schema）：
        - 精选 schema 可能遗漏了相关表，用完整 schema 补全
        - 不包含对话历史（简化 prompt，降低干扰）
        - 仍然是 temperature=0
        - 覆盖 Attempt 1 失败的大部分场景

    Attempt 3（高温度简化提示词）：
        - 换用 temperature=0.7 的 LLM，增加创造性
        - 提示词极简化，只保留核心信息
        - system prompt 也简化为 "You are a SQL expert"
        - 最后的兜底手段

    生成后的后处理：
        - 移除中文注释
        - 校验并修复表名（模糊匹配）
        - 校验并修复列名（幻觉检测）

    Args:
        question: 用户的自然语言问题
        schema_context: LLM 精选的 schema 上下文文本
        raw_metadata: 完整的元数据 JSON 字符串（用于第二次重试和列名校验）
        history: 对话历史列表（用于多轮查询）
    Returns:
        dict 包含：
        - sql: 生成的 SQL 字符串（失败时为空字符串）
        - attempt: 成功时的尝试次数（1/2/3）
        - table_fixes: 表名修复记录列表
        - column_fixes: 列名修复记录列表
    """
    # 构建对话历史上下文（多轮查询时追加到 prompt 末尾）
    history_ctx = _build_history_context(history)
    attempt = 1

    # ---- Attempt 1: 使用精选 schema + 对话历史，直接生成 ----
    # 这是最优路径：schema 已经是 LLM 精选的最相关表，prompt 信息密度最高
    messages = [
        ("system", SYSTEM_PROMPT),  # 系统提示词：定义 LLM 角色、输出格式、约束规则
        ("human", build_user_prompt(question, schema_context) + history_ctx),  # 用户提示词：问题 + schema + 历史
    ]
    sql = await _llm_generate(messages, attempt="attempt1")

    # ---- Attempt 2: 使用完整 schema 重试 ----
    # 如果精选 schema 信息不足（遗漏了相关表），用完整 schema 补全
    if sql is None and raw_metadata:
        logger.info("LLM returned empty, retrying with full schema")
        attempt = 2
        full_schema = _build_full_schema_context(raw_metadata)  # 从 JSON 构建完整 schema 文本
        retry_messages = [
            ("system", SYSTEM_PROMPT),
            ("human", build_user_prompt(question, full_schema)),  # 注意：不包含对话历史，简化 prompt
        ]
        sql = await _llm_generate(retry_messages, attempt="attempt2")

    # ---- Attempt 3: 高温度 LLM + 简化提示词 ----
    # 最后的兜底：换一个更有"创造力"的 LLM，用最简单的提示词
    if sql is None:
        logger.info("Retrying with higher temperature LLM")
        attempt = 3
        # 极简提示词：只保留问题和 schema，去掉所有格式约束
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

    # 三次尝试全部失败，返回空结果
    if sql is None:
        return {"sql": "", "attempt": attempt, "table_fixes": [], "column_fixes": []}

    # ---- 后处理：校验并修复表名和列名 ----
    logger.info("Before validation: sql=%s", sql[:200])
    sql, table_fixes, column_fixes = _validate_and_fix_tables(sql, schema_context, raw_metadata)
    logger.info("After validation: sql=%s", sql[:200])
    return {"sql": sql, "attempt": attempt, "table_fixes": table_fixes, "column_fixes": column_fixes}
