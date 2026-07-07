"""
Chat API shared utilities — deduplicated helpers used by chat.py and chat_stream.py.

Functions moved here to avoid duplication across the synchronous and streaming chat endpoints.
"""
from __future__ import annotations

import logging
from decimal import Decimal
import datetime
from enum import Enum
from uuid import UUID

logger = logging.getLogger(__name__)


def normalize_value(v):
    """DB 行值 → JSON 安全类型 (Decimal/datetime/UUID/Enum/bytes/其他复杂对象)。

    对未知类型走 str() 兜底, 保证 json.dumps 能序列化。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v  # bool 是 int 子类, 必须在 int 之前判断
    if isinstance(v, (int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    # 兜底: 其他不可序列化类型 (numpy/自定义对象) 转 str, 避免 json.dumps 抛 TypeError
    return str(v)


def serialize_thinking(thinking) -> dict | None:
    """把 ThinkingResult 序列化为 dict (供 ConversationState.thinking 持久化)。

    没有 thinking 或解析失败时返回 None (历史对话恢复时显示为空)。
    """
    if not thinking:
        return None
    if hasattr(thinking, "tables"):
        return {
            "tables": list(getattr(thinking, "tables", [])),
            "aggregation": getattr(thinking, "aggregation", "") or "",
            "caveats": list(getattr(thinking, "caveats", [])),
            "prev_sql_review": getattr(thinking, "prev_sql_review", "") or "",
        }
    if isinstance(thinking, dict):
        return thinking
    return None


def build_schema_context_fallback(models: list[dict]) -> str:
    """兜底: 语义层为空时, 从检索结果 text 构建 schema_context。

    正常路径用 schema_utils.build_schema_context (从语义层完整定义),
    这个仅当 semantic_content 缺失时兜底 (不靠正则猜列名)。
    """
    if not models:
        return ""
    lines = [f"{m.get('name', '')}: {m.get('text', '')}" for m in models]
    return "\n".join(lines)


def inherit_prev_tables(
    prev_tables: list[str],
    current_names: list[str],
    semantic_content: object | None,
) -> list[str]:
    """追问表继承: 将上轮涉及的表合并进当前检索结果。

    规则:
      - 只继承语义层中有定义的表 (不在语义层中的表可能已删除/重命名, 忽略)
      - 跳过 current_names 中已存在的表 (去重)
      - 返回新的 list, 不修改原 current_names

    Args:
        prev_tables: 上轮对话涉及的表名列表
        current_names: 当前检索已命中的表名列表
        semantic_content: SemanticModelContent 对象 (需有 .models 属性)

    Returns:
        合并后的表名列表 (新 list)
    """
    if not prev_tables:
        return current_names

    result = list(current_names)
    semantic_names = set()
    if semantic_content and hasattr(semantic_content, "models"):
        semantic_names = {m.get("name", "") for m in semantic_content.models}

    for t in prev_tables:
        if not t or t in result:
            continue
        if t not in semantic_names:
            logger.debug("追问表继承: '%s' 不在语义层中, 忽略", t)
            continue
        result.append(t)

    return result
