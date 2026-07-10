"""
T032: SQL 自愈 — 错误码映射 + 专项纠正 prompt + 熔断器

对标:
  - AEE-002: 错误码映射(10+) + 专项纠正 + 2轮上限 + 熔断器
  - v1 教训 #32: 自愈 prompt 必须保留全部安全规则
    (v1 self_heal.py 只写"你只生成SQL" → 能出 DROP TABLE)
  - Claude Code §6.4: 熔断器 (跨查询连续失败停止, 不无限重试)

错误码映射 (spec AEE-002):
  1146 表不存在 / 1054 列不存在 / 1064 语法 / 1052 歧义列

设计要点:
  - heal_sql: 错误信息 → 提取错误码 → 映射类别 → 专项纠正 prompt → LLM → 走同样三层校验
  - 自愈 prompt 保留 T029 的全部安全规则 (SELECT only + 白名单 + 危险函数 + data_type)
  - 熔断器: 跨查询连续 N 次失败 → 停止自愈 (不调 LLM)
  - 单次自愈失败不算熔断 (熔断是跨查询累积)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from app.core.sql_validator import ValidationResult, validate_sql

logger = logging.getLogger(__name__)


# ── 错误码提取 + 类别映射 ─────────────────────────────────────

# 错误码 → 类别 (对标 spec AEE-002)
_ERROR_CODE_MAP = {
    "1146": "TABLE_NOT_EXIST",
    "1051": "TABLE_NOT_EXIST",  # Unknown table
    "1054": "COLUMN_NOT_EXIST",
    "1166": "COLUMN_NOT_EXIST",
    "1064": "SYNTAX_ERROR",
    "1149": "SYNTAX_ERROR",
    "1052": "AMBIGUOUS_COLUMN",
    "1060": "AMBIGUOUS_COLUMN",
    "1066": "DUPLICATE_TABLE_ALIAS",
}


class ErrorCategory(str, Enum):
    TABLE_NOT_EXIST = "TABLE_NOT_EXIST"
    COLUMN_NOT_EXIST = "COLUMN_NOT_EXIST"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    AMBIGUOUS_COLUMN = "AMBIGUOUS_COLUMN"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_error_code(cls, code: str | None) -> "ErrorCategory":
        if not code:
            # PG 错误无数字码, 从文字判断
            return cls.UNKNOWN
        cat = _ERROR_CODE_MAP.get(code)
        return cls(cat) if cat else cls.UNKNOWN


def extract_error_code(error: str) -> str | None:
    """从错误信息提取数字错误码。

    MySQL: "(1146, \"Table doesn't exist\")" → "1146"
    PG: relation/column does not exist → 无数字码, 返回 None (靠类别从文字推断)
    """
    if not error:
        return None
    # MySQL 风格 (code, msg)
    m = re.search(r"\((\d{4}),", error)
    if m:
        return m.group(1)
    return None


# ── 熔断器 (对标 Claude Code §6.4) ────────────────────────────

class SelfHealCircuitBreaker:
    """跨查询连续失败熔断器 (三态: closed / open / half-open)。

    closed: 正常工作, 记录失败/成功
    open: 连续失败 >= threshold, 拒绝自愈 (不调 LLM)
    half-open: open 后经过 cooldown 秒, 允许一次试探; 成功→closed, 失败→open

    对标 Claude Code §6.4 + 标准熔断器模式 (避免永久锁死)。
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: int = 60):
        import time
        self._threshold = threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._tripped_at: float = 0.0  # 熔断时刻 (用于 half-open 判断)

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            import time
            self._tripped_at = time.monotonic()
            logger.error(
                "自愈熔断器触发: 连续 %d 次失败 (>= %d), 停止自愈",
                self._consecutive_failures, self._threshold,
            )

    def record_success(self) -> None:
        """自愈成功 → 重置 (closed 状态)。"""
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0
            self._tripped_at = 0.0

    def is_tripped(self) -> bool:
        """是否熔断 (含半开恢复: cooldown 后放行一次试探)。"""
        if self._consecutive_failures < self._threshold:
            return False
        # open 状态: 检查是否过了 cooldown → half-open (放行试探)
        import time
        if self._tripped_at and (time.monotonic() - self._tripped_at) > self._cooldown_seconds:
            logger.info("自愈熔断器进入 half-open, 允许试探一次")
            self._tripped_at = 0.0  # 标记已进入 half-open (下一次结果决定 closed/open)
            return False
        return True

    def reset(self) -> None:
        self._consecutive_failures = 0


# ── 自愈 ──────────────────────────────────────────────────────

# 模块级单例熔断器 (跨查询共享)
_circuit_breaker: SelfHealCircuitBreaker | None = None


def get_circuit_breaker() -> SelfHealCircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        from app.core.config import get_settings
        _circuit_breaker = SelfHealCircuitBreaker(
            threshold=get_settings().sql_self_heal_circuit_breaker,
        )
    return _circuit_breaker


def reset_circuit_breaker() -> None:
    """重置熔断器 (测试用)。"""
    global _circuit_breaker
    _circuit_breaker = None


@dataclass
class HealResult:
    """自愈结果。"""
    success: bool = False
    sql: str = ""
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(ok=False, reason="未自愈"))
    error: str | None = None
    rounds: int = 0


# 自愈安全规则 (对标 v1 教训 #32: 必须保留全部安全规则)
_SECURITY_RULES = """严格规则 (违反则拒绝):
1. 只能生成 SELECT 语句, 禁止 INSERT/UPDATE/DELETE/DROP/ALTER 等写操作
2. 只能使用下方"允许的列"里的列名, 禁止臆造
3. 禁止危险函数: LOAD_FILE/SLEEP/BENCHMARK/INTO OUTFILE
4. 遵守 data_type 约束 (不对 VARCHAR 做 SUM)
5. 只返回 SQL, 不要解释
6. 为每个 SELECT 输出列添加 AS 中文别名 (schema 中有中文名的用中文名, 聚合列也要有中文别名)"""

# 专项纠正提示 (按错误类别)
_CATEGORY_HINTS = {
    ErrorCategory.COLUMN_NOT_EXIST: "错误原因: 列名不存在。请从'允许的列'里选正确的列名, 不要臆造。",
    ErrorCategory.TABLE_NOT_EXIST: "错误原因: 表不存在。请检查 schema 里可用的表名。",
    ErrorCategory.SYNTAX_ERROR: "错误原因: SQL 语法错误。请修正语法。",
    ErrorCategory.AMBIGUOUS_COLUMN: "错误原因: 列名歧义 (多张表有同名列)。请用'表名.列名'格式明确。",
    ErrorCategory.UNKNOWN: "错误原因: SQL 执行失败。请根据错误信息修正。",
}


async def heal_sql(
    sql: str,
    error: str,
    allowed_columns: set[str],
    schema_context: str,
    circuit_breaker: SelfHealCircuitBreaker | None = None,
) -> HealResult:
    """根据执行错误自愈 SQL。

    Args:
        sql: 执行失败的 SQL
        error: 执行错误信息 (含错误码)
        allowed_columns: 白名单列
        schema_context: schema 上下文
        circuit_breaker: 熔断器 (None → 全局单例)

    Returns:
        HealResult — success=True 表示自愈成功且通过三层校验
    """
    cb = circuit_breaker or get_circuit_breaker()

    # 熔断 → 直接返回失败, 不调 LLM (避免浪费)
    if cb.is_tripped():
        logger.warning("自愈熔断器已触发, 跳过自愈")
        return HealResult(error="自愈熔断器已触发 (连续失败过多)")

    # 错误分析
    code = extract_error_code(error)
    category = ErrorCategory.from_error_code(code)
    hint = _CATEGORY_HINTS.get(category, _CATEGORY_HINTS[ErrorCategory.UNKNOWN])

    # ── 自愈 prompt (保留全部安全规则, 对标 v1 #32) ───────────
    from app.core.llm_client import llm_chat

    # SEC (对标 S6): 仅保留错误类别信息, 不传原始 DB 错误 (防泄露跨租户 schema)
    # 原始错误可能含 "Table 'tenant_xxx.table' doesn't exist" 等跨租户信息
    # LLM 只需知道错误类别 + hint 即可修正, 不需要原始错误文本
    # 用类别提示替代原始错误 (更安全, LLM 仍能修正)
    error_for_prompt = f"[{category.value}] {hint}"
    # 如果有错误码, 附上 (code 已在上方 extract_error_code 提取)
    if code:
        error_for_prompt = f"[错误码 {code}] {error_for_prompt}"

    prompt = (
        f"你是 BI SQL 修正器。下面这条 SQL 执行失败了, 请修正。\n\n"
        f"{_SECURITY_RULES}\n\n"
        f"【schema】{schema_context}\n"
        f"【允许的列】{', '.join(sorted(allowed_columns))}\n\n"
        f"【失败的 SQL】{sql}\n"
        f"【错误信息】{error_for_prompt}\n"
        f"【纠正方向】{hint}\n\n"
        f"只返回修正后的 SQL:"
    )

    try:
        content, _ = await llm_chat(
            messages=[{"role": "user", "content": prompt}],
            node="heal_sql",
            temperature=0.0,
        )
    except Exception as e:
        logger.warning("自愈 LLM 调用失败: %s", e)
        cb.record_failure()
        return HealResult(error=f"自愈 LLM 调用失败: {e}")

    # 提取 SQL (复用 sql_agent 的提取逻辑)
    from app.ai.sql_agent import _extract_sql
    healed = _extract_sql(content)
    if not healed:
        cb.record_failure()
        return HealResult(error="自愈 LLM 未返回有效 SQL")

    # ── 走同样的三层校验 (v1 教训 #32: 不信任自愈结果) ────────
    validation = validate_sql(healed, allowed_columns)
    if not validation.ok:
        logger.warning("自愈 SQL 校验失败 (%s): %s", validation.violated_layer, validation.reason)
        cb.record_failure()
        return HealResult(
            sql=healed, validation=validation,
            error=f"自愈 SQL 校验失败: {validation.reason}",
        )

    # 自愈成功 → 重置熔断器
    cb.record_success()
    logger.info("自愈成功 (category=%s): %s", category.value, healed[:80])
    return HealResult(success=True, sql=healed, validation=validation, rounds=1)
