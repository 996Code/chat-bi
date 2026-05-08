"""
SQL 自愈节点 —— 执行失败时分析错误并重试修正。

=== 文件定位 ===
本文件是 LangGraph AI 管道中「自愈」环节的核心实现。
整个 AI 查询流程为：
  用户提问 → 意图识别 → 上下文补全 → Schema 选择 → SQL 生成 → 执行查询
  → ★ SQL 自愈（本文件）→ 图表推断 → 返回结果

当 SQL 执行失败时，本节点会：
  1. 从错误信息中提取 MySQL 错误码
  2. 将错误上下文组装成 prompt，让 LLM 重新生成 SQL
  3. 执行修复后的 SQL，若仍失败则继续重试（最多 2 轮）
  4. 超过重试上限则返回失败

=== 核心概念 ===
- **迭代重试模式**：while 循环 + retry_count 计数器，每轮用上一轮的错误信息
  指导 LLM 修正，形成「失败 → 诊断 → 修复 → 再执行」的闭环。
- **错误码映射**：MySQL 返回的错误信息包含数字错误码（如 1054 = 列不存在），
  本文件用正则提取错误码，再查 AUTO_FIX_RULES 得到中文描述，帮助 LLM 精准定位问题。
- **与 execution.py 的关系**：本文件调用 execute_sql() 来验证修复后的 SQL 是否正确，
  形成自愈循环的「执行 → 反馈」环节。

=== 关联文件 ===
- app/ai/nodes/execution.py  — 提供 execute_sql()，实际执行 SQL 并返回结果
- app/ai/nodes/shared_utils.py — 提供 get_llm()，获取 LLM 实例
- app/core/config.py          — settings.llm_self_heal_max_retries 控制最大重试次数
"""
# asyncio：用于设置 LLM 调用的超时控制（asyncio.timeout）
import asyncio
# re：正则表达式模块，用于从 MySQL 错误信息中提取数字错误码
import re
# Any：类型提示，表示返回值可以是任意类型
from typing import Any

# settings：全局配置对象，llm_self_heal_max_retries 等参数从这里读取
from app.core.config import settings
# get_logger：项目统一的日志工具，用 __name__ 作为日志标识符
from app.core.logging import get_logger
# get_llm：获取 LLM 实例的工厂函数，屏蔽了不同模型供应商的差异
from app.ai.nodes.shared_utils import get_llm

logger = get_logger(__name__)

# ── MySQL 错误码提取用的正则表达式 ──
# MySQL 的错误信息有两种常见格式：
#   1. "(errno: 1054)" — SQLAlchemy 封装后的格式，含 "errno:" 前缀
#   2. "(1054)"        — 原生 MySQL 错误格式，纯数字在括号内
# 我们需要两个正则分别匹配，且必须先试 errno 格式（更精确），再试通用格式

# 匹配 "(errno: 1054)" 格式 —— \s* 允许冒号后有空格
_ERRNO_RE = re.compile(r'\(errno:\s*(\d+)\)')
# 匹配 "(1054)" 格式 —— \d{3,4} 限制 3~4 位数字，避免误匹配其他括号内容
_GENERIC_RE = re.compile(r'\((\d{3,4})\)')

# 可自动修复的常见错误模式
# key 是 MySQL 错误码（字符串），value 是中文描述
# 这些错误码覆盖了 LLM 生成 SQL 时最常犯的几类错误：
#   1146 — 引用了不存在的表（LLM 编造了表名）
#   1054 — 引用了不存在的列（LLM 猜测了列名）
#   1064 — SQL 语法错误（LLM 生成了不合法的 SQL）
#   1049 — 数据库名错误（LLM 用了错误的数据库）
# 将错误码映射为中文描述后，prompt 中会告诉 LLM "错误类型：列不存在"，
# 而非原始的英文错误信息，帮助 LLM 更精准地修正
AUTO_FIX_RULES = {
    "1146": "表不存在",
    "1054": "列不存在",
    "1064": "SQL 语法错误",
    "1049": "数据库不存在",
}


def extract_error_code(error_msg: str) -> str:
    """
    从 MySQL 错误消息中提取错误码。

    策略：先尝试匹配 "(errno: 数字)" 格式（更精确），再尝试 "(数字)" 格式。
    如果都匹配不到，返回空字符串。

    参数:
        error_msg: MySQL 返回的完整错误信息字符串

    返回:
        错误码字符串（如 "1054"），未匹配到则返回 ""

    Python 知识点:
        - re.compile().search() 在字符串中查找第一个匹配
        - m.group(1) 返回正则中第一个括号（捕获组）的内容
        - 函数通过 if 逐级降级匹配，是"优先精确、兜底模糊"的常见模式
    """
    m = _ERRNO_RE.search(error_msg)
    if m:
        return m.group(1)
    m = _GENERIC_RE.search(error_msg)
    if m:
        return m.group(1)
    return ""


def build_fix_prompt(question: str, failed_sql: str, error: str, retry_count: int, schema_context: str) -> str:
    """
    构建 SQL 修正 prompt —— 将错误上下文格式化为 LLM 可理解的修复指令。

    设计思路：
      LLM 修复 SQL 的关键在于"上下文充分"。本函数把以下信息组装进 prompt：
        - 原始问题：让 LLM 理解用户意图
        - 失败的 SQL：让 LLM 知道哪里出了问题
        - 错误信息 + 错误类型：精确诊断问题根因
        - 重试次数：多次重试时提醒 LLM 更仔细
        - 表结构：提供正确的列名/表名，避免 LLM 再次编造

    参数:
        question:       用户原始自然语言问题
        failed_sql:     上一次执行失败的 SQL
        error:          MySQL 返回的错误信息原文
        retry_count:    当前是第几次重试（从 1 开始）
        schema_context: 可用的表结构信息（DDL），为空则不附带

    返回:
        完整的 prompt 字符串

    Python 知识点:
        - f-string 中嵌套三元表达式：{x if cond else y}
        - dict.get(key, default)：安全取值，key 不存在时返回 default
        - error[:200]：字符串切片，截取前 200 字符防止过长
    """
    # 提取错误码并映射为中文描述；未命中映射则取错误信息前 200 字符
    error_code = extract_error_code(error)
    error_desc = AUTO_FIX_RULES.get(error_code, error[:200])

    schema_section = f"可用的表结构：\n{schema_context}" if schema_context else ""
    retry_hint = f"这是第 {retry_count} 次重试，请仔细检查。" if retry_count > 1 else ""

    return f"""你是 SQL 修复专家。请修复以下 SQL 的错误。

原始问题：{question}
失败的 SQL：{failed_sql}
错误信息：{error}
错误类型：{error_desc}

{retry_hint}

{schema_section}

请只返回修复后的 SQL，不要包含任何解释或 markdown 代码块。"""


def _strip_markdown(raw: str) -> str:
    """
    去除 LLM 返回内容中的 markdown 代码块标记。

    LLM 经常返回 ```sql ... ``` 格式的代码块，但我们需要纯 SQL 文本。
    此函数用正则去掉开头的 ```sql（或 ```）和结尾的 ```。

    参数:
        raw: LLM 返回的原始文本

    返回:
        去除 markdown 标记后的纯 SQL 字符串

    Python 知识点:
        - re.sub(pattern, replacement, string, flags)：替换匹配内容
        - flags=re.MULTILINE：让 ^ 和 $ 匹配每行的开头/结尾（而非整个字符串）
        - (?:\\w+)?：非捕获组 + 可选，匹配 "sql" 等语言标识符但不捕获
        - .strip()：去除首尾空白字符
    """
    # 去除开头的 ```sql 或 ```
    sql = re.sub(r'^```(?:\w+)?\s*', '', raw, flags=re.MULTILINE).strip()
    # 去除结尾的 ```
    sql = re.sub(r'\s*```\s*$', '', sql).strip()
    return sql


async def self_heal_sql(
    question: str,
    sql: str,
    error: str,
    datasource_id: str = "",
    schema_context: str = "",
    dialect: str = "mysql",
    retry_count: int = 1,
) -> dict[str, Any]:
    """
    迭代式修复失败的 SQL —— 自愈循环的核心函数。

    工作流程（while 循环实现迭代重试）：
      1. 构建修复 prompt（包含错误信息 + 表结构）
      2. 调用 LLM 生成修复后的 SQL
      3. 执行修复后的 SQL
      4. 若成功 → 返回结果；若失败 → 用新错误信息进入下一轮重试
      5. 超过最大重试次数 → 返回失败

    参数:
        question:       用户原始自然语言问题
        sql:            上一次执行失败的 SQL
        error:          MySQL 返回的错误信息
        datasource_id:  数据源 ID，传给 execute_sql() 确定查询目标
        schema_context: 可用的表结构信息（DDL 文本）
        dialect:        SQL 方言，默认 "mysql"
        retry_count:    当前重试次数（从 1 开始）

    返回:
        成功时: {"success": True, "sql": 修复后的SQL, "fixed": True, ...执行结果}
        失败时: {"success": False, "error": "SQL 修复失败（已重试 N 次）"}

    Python 知识点:
        - async def：定义异步函数，内部可用 await
        - dict[str, Any]：Python 3.9+ 的类型注解写法（等价于 Dict[str, Any]）
        - {**result}：字典解包，将 result 的所有键值对合并到新字典中
        - 延迟导入（函数内 import）：避免循环依赖，execution.py 可能也引用本模块
    """
    # 延迟导入：避免 self_heal.py 和 execution.py 之间的循环引用
    # 这是 Python 中解决循环依赖的常见手法——把 import 放在函数内部
    from app.ai.nodes.execution import execute_sql

    # ── 自愈主循环：最多重试 llm_self_heal_max_retries 次（默认 2） ──
    # 设计权衡：重试太少可能错过可修复的错误，太多则浪费 LLM 调用并增加延迟
    # 2 次是经验值——第一轮修复常见错误（列名/表名），第二轮处理更复杂的情况
    while retry_count <= settings.llm_self_heal_max_retries:
        # 构建修复 prompt：将错误上下文格式化为 LLM 可理解的修复指令
        prompt = build_fix_prompt(question, sql, error, retry_count, schema_context)

        try:
            # 获取 LLM 实例（每次循环都重新获取，确保配置变更能生效）
            llm = get_llm()
            # asyncio.timeout(30)：设置 30 秒超时，防止 LLM 调用卡死
            # 这是 Python 3.11+ 的异步超时上下文管理器，超时后抛出 TimeoutError
            async with asyncio.timeout(30):
                # llm.ainvoke()：异步调用 LLM，传入消息列表
                # 消息格式 [("system", ...), ("human", ...)] 是 LangChain 的标准格式
                # system 消息设定角色，human 消息包含具体 prompt
                response = await llm.ainvoke([
                    ("system", "你只生成 SQL，不解释。"),
                    ("human", prompt),
                ])
            # LLM 可能返回 ```sql ... ``` 格式，需要去除 markdown 标记
            fixed_sql = _strip_markdown(response.content)

            # LLM 返回空内容时的防御性处理
            if not fixed_sql:
                logger.warning("Self-heal LLM returned empty, retry %d", retry_count)
                retry_count += 1
                continue  # 跳过本轮，进入下一轮重试

            # 执行修复后的 SQL，验证是否真正修复成功
            result = await execute_sql(fixed_sql, datasource_id, dialect)

            if result["success"]:
                # 修复成功！记录日志并返回结果
                # {**result} 将 execute_sql 返回的所有字段（如 data, columns）合并进来
                logger.info("SQL self-heal succeeded on retry %d: %s", retry_count, fixed_sql[:200])
                return {"success": True, "sql": fixed_sql, "fixed": True, **result}

            # 修复后仍然失败——用新的错误信息更新 error 变量，下一轮重试时 LLM 能看到
            # 这是自愈循环的关键：每轮的错误信息都会反馈给 LLM，形成"错误 → 诊断 → 修复"闭环
            logger.info("SQL fix attempt %d still failed: %s", retry_count, result.get("error"))
            error = result.get("error", "")
            retry_count += 1

        except Exception as e:
            # LLM 调用本身可能失败（网络超时、API 限流等），此时不算修复失败
            # 只记录错误并进入下一轮重试
            logger.error("Self-healing LLM call failed: %s", e)
            retry_count += 1

    # ── 超过最大重试次数，放弃修复 ──
    # 返回失败结果，上层调用者（LangGraph 节点）会将此错误展示给用户
    logger.warning("SQL self-healing exceeded max retries for: %s", sql[:200])
    return {"success": False, "error": f"SQL 修复失败（已重试 {settings.llm_self_heal_max_retries} 次）"}
