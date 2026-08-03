"""
Chat API shared utilities — deduplicated helpers used by chat.py and chat_stream.py.

Functions moved here to avoid duplication across the synchronous and streaming chat endpoints.

对标:
  - 数据序列化: 确保 DB 行值可被 json.dumps 序列化 (前端消费)
  - 继承逻辑: 追问场景下跨轮次表继承 (对标 AEE-001 step 6-7)
  - 兜底策略: 语义层缺失时的 schema_context 降级构造

数据流总览:
  normalize_value:    DB 查询结果 → JSON 安全类型 → 前端渲染
  serialize_thinking: ThinkingResult → dict → ConversationState 持久化
  inherit_prev_tables: 上轮表的继承 → 当前检索表合并 → SQL 生成上下文
  build_schema_context_fallback: 语义层缺失 → 检索文本降级 → SQL 生成 prompt
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

    数据流:
      1. 输入: DB 查询返回的原始值 (可能为各种 Python 类型)
      2. 转换: 按类型映射到 JSON 安全类型
      3. 输出: 可被 json.dumps 直接序列化的值

    边界情况:
      - bool 是 int 子类, 必须在 int 之前判断 (否则 True 会被转为 1)
      - Decimal 转 float 可能有精度损失, 但 BI 展示场景可接受
      - bytes 按 utf-8 解码, errors=replace 确保不抛异常
      - 兜底 str() 覆盖 numpy 类型、自定义对象等不可预见类型
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

    数据流:
      1. 输入: ThinkingResult 实例 (或 dict)
      2. 转换: 提取 tables/aggregation/caveats/prev_sql_review 字段
      3. 输出: dict 或 None (持久化到 ConversationState)

    设计决策:
      - 只序列化结构化字段, 不序列化 LLM 原始输出 (减少存储开销)
      - 支持 dict 输入 (兼容已序列化的数据反序列化后再次序列化)
      - 返回 None 标识"无 thinking", 与空 dict 区分 (空 dict 表示 thinking 已初始化但无内容)
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

    设计原则:
      - 不做正则提取列名 (容易漏匹配和误匹配)
      - 只使用检索器返回的原始 text 字段 (含表名 + 描述)
      - 这是安全降级, 不保证列名完整 — 正常路径应由语义层提供

    数据流:
      semantic_content 缺失 → 调用此函数 → 拼接 models text → 注入 SQL 生成 prompt
    """
    if not models:
        return ""
    lines = [f"{m.get('name', '')}: {m.get('text', '')}" for m in models]
    return "\n".join(lines)


def _extract_model_name(m: object) -> str:
    """从语义层 Model 对象或 dict 中提取表名 (兼容 Pydantic BaseModel 和 dict)。"""
    if isinstance(m, dict):
        return m.get("name", "")
    return getattr(m, "name", "")


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

    设计背景:
      用户在追问场景下 (如"看看这个的明细"), 上轮涉及的表应自动继承,
      避免用户每次都要完整表述。但继承的表必须经过语义层校验,
      防止表被删除或重命名后仍被引用。

    数据流:
      agent.py 追问判断 → 从 ConversationState 读 prev_tables →
      调用 inherit_prev_tables → 合并到 current_names → SQL 生成

    Args:
        prev_tables: 上轮对话涉及的表名列表
        current_names: 当前检索已命中的表名列表
        semantic_content: SemanticModelContent 对象 (需有 .models 属性,
            元素可以是 Pydantic Model 或 dict)

    Returns:
        合并后的表名列表 (新 list)
    """
    if not prev_tables:
        return current_names

    result = list(current_names)
    semantic_names: set[str] = set()
    if semantic_content and hasattr(semantic_content, "models"):
        semantic_names = {_extract_model_name(m) for m in semantic_content.models}

    for t in prev_tables:
        if not t or t in result:
            continue
        if t not in semantic_names:
            logger.debug("追问表继承: '%s' 不在语义层中, 忽略", t)
            continue
        result.append(t)

    return result
