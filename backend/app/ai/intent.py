"""
T026: 意图识别 — 用户问题分流到 5 种意图

对标:
  - INT-001 (openspec spec): 5 种意图
  - INT-002: Pydantic 强约束 + confidence<0.6 降级 + schema 重试
  - INT-004: normalized_question 剥离可视化措辞

5 意图:
  TEXT_TO_SQL  完整 SQL 生成管道
  CLARIFICATION 需要上下文补全/追问 (低置信度也降级到此)
  GENERAL      闲聊, 不碰 DB
  CHART_MODIFY 只改图表类型, 不改 SQL
  EXPLANATION  解释已有 SQL/结果

设计 (Fail-Closed):
  - LLM 输出不符合 Pydantic schema → 重试 (max 2 次)
  - 重试耗尽 / LLM 失败 → 降级 CLARIFICATION (宁缺毋滥, 不瞎猜走 SQL 管道)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

IntentType = Literal["TEXT_TO_SQL", "CLARIFICATION", "GENERAL", "CHART_MODIFY", "EXPLANATION"]

# confidence 降级阈值 (对标 INT-002)
CONFIDENCE_DEGRADE_THRESHOLD = 0.6
# schema 不匹配重试次数 (对标 INT-002: max 2 次)
MAX_RETRIES = 2


class IntentOutput(BaseModel):
    """意图识别输出 (Pydantic 强约束, 对标 INT-002)。

    LLM 必须返回此结构, 否则重试/降级。
    """
    intent: IntentType
    normalized_question: str = Field(description="剥离可视化措辞 + 展开追问指代后的完整独立问题")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    chart_type_hint: str | None = Field(
        default=None,
        description="图表类型提示 (line/bar/pie/scatter), 仅 CHART_MODIFY/带可视化措辞时有值",
    )


# ── 可视化措辞剥离 (对标 INT-004) ─────────────────────────────

# 可视化关键词 → chart_type_hint 映射
_VISUALIZATION_PATTERNS = [
    (re.compile(r"用?(?:折线图|线图|曲线图|line chart)", re.IGNORECASE), "line"),
    (re.compile(r"用?(?:柱状图|柱图|条形图|bar chart)", re.IGNORECASE), "bar"),
    (re.compile(r"用?(?:饼图|饼状图|pie chart)", re.IGNORECASE), "pie"),
    (re.compile(r"用?(?:散点图|scatter)", re.IGNORECASE), "scatter"),
    (re.compile(r"画成|展示为|换成|改成|用.*(?:图|chart)展示", re.IGNORECASE), None),
]


def strip_visualization(question: str, return_hint: bool = False) -> str | tuple[str, str | None]:
    """从问题里剥离可视化措辞, 保留业务过滤条件 (对标 INT-004)。

    NOTE: 目前未接入 classify_intent 流程 (LLM 直接在 JSON 里返回 chart_type_hint),
    作为规则回退保留, 待 LLM 对图表类型判断不准时接入。

    "用折线图展示本月销售额" → ("本月销售额", "line")
    "画成饼图 各品类占比" → ("各品类占比", "pie")

    Args:
        question: 原始问题
        return_hint: 是否返回 chart_type_hint
    """
    result = question
    hint = None
    for pattern, chart_type in _VISUALIZATION_PATTERNS:
        if pattern.search(result):
            if chart_type and not hint:
                hint = chart_type
            result = pattern.sub("", result)
    # 清理残留的可视化动词 (展示/画成/换成/改成 等已在 pattern 里, 但单独的"展示"要补)
    result = re.sub(r"^(展示|显示|看看|看下)\s*", "", result)
    # 清理多余空格
    result = re.sub(r"\s+", " ", result).strip()
    if return_hint:
        return result, hint
    return result


# ── 意图分类 ──────────────────────────────────────────────────

_INTENT_PROMPT = """你是 BI 系统的意图识别器。判断用户问题的意图, 只返回 JSON。

5 种意图:
- TEXT_TO_SQL: 查询数据。包括基于对话历史的追问和复用——只要能从历史或当前问题中
  明确知道要查什么数据, 就是 TEXT_TO_SQL (如"本月销售额"、"上个月呢"、"再查一次"、
  "换成北京的数据")。
- CLARIFICATION: 无法确定用户要查什么。即: 当前问题模糊, 且对话历史也无法消解
  (如没有上下文时的"那个呢"、"然后呢")。只要历史里有明确的上轮查询, 就不该判此意图。
- GENERAL: 闲聊/打招呼, 不涉及数据查询 (如"你好"、"谢谢")
- CHART_MODIFY: 只修改图表展示方式, 不改数据 (如"换成饼图"、"用柱状图")
- EXPLANATION: 解释已有 SQL 或结果 (如"这个查询什么意思"、"为什么是这个数")

核心原则: 如果对话历史里有上轮查询, 用户的追问/复用/修改条件都属于 TEXT_TO_SQL,
不是 CLARIFICATION。只有完全无法判断用户意图时才用 CLARIFICATION。

规则:
1. normalized_question 的生成分两步: 先剥离可视化措辞 ("用折线图展示本月销售" → "本月销售"),
   再展开追问指代 ("环比" → "本月各品类销售额的环比")。最终结果必须是不依赖对话上下文
   也能独立理解的完整问题。不要只返回追问原文。
2. chart_type_hint: 如果含可视化措辞, 提取图表类型 (line/bar/pie/scatter), 否则 null
3. confidence: 你对这个意图判断的置信度 (0-1)

只返回 JSON, 格式: {"intent": "...", "normalized_question": "...", "confidence": 0.9, "reason": "...", "chart_type_hint": "line"}
"""


async def classify_intent(question: str, history: str | None = None) -> IntentOutput:
    """调 LLM 识别意图, 失败/低置信度降级 CLARIFICATION。

    Args:
        question: 用户原始问题
        history: 多轮对话历史文本 (追问时注入, 让"再查一遍/上个月呢"能正确消解指代
                 并判为 TEXT_TO_SQL 而非 CLARIFICATION; 对标 ARC-04)

    Returns:
        IntentOutput — 始终返回 (不抛), 低置信/失败降级 CLARIFICATION
    """
    from app.core.config import get_settings
    from app.core.llm_client import llm_chat
    from app.core.text_sanitize import sanitize_text

    # SEC-007: 用户输入进 LLM 前清洗 (NFKC + 去零宽/方向控制字符)
    clean_question = sanitize_text(question)
    # 追问时把历史拼进 user message (让意图识别结合上下文判断)
    user_content = clean_question
    if history:
        user_content = f"【对话历史】\n{history}\n\n【当前问题】{clean_question}"
    # 有些模型不认 system 角色, 把格式指令也放进 user message
    user_content = (
        f"{_INTENT_PROMPT}\n\n"
        f"用户问题: {user_content}\n\n"
        f"只返回 JSON, 不要解释:"
    )

    messages = [
        {"role": "system", "content": _INTENT_PROMPT},
        {"role": "user", "content": user_content},
    ]

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            content, resp = await llm_chat(
                messages=messages, node="intent", temperature=0.0,
            )
            # Debug: 记录 LLM 原始返回, 排查非 JSON 问题
            logger.debug("意图识别 LLM 原始返回 (attempt %d): %r", attempt + 1, content[:500])
            from app.core.llm_json import parse_json_response
            parsed = parse_json_response(content)
            if parsed is None:
                raise ValueError("LLM 未返回有效 JSON")
            output = IntentOutput(**parsed)

            # confidence < 阈值 → 降级 CLARIFICATION
            if output.confidence < CONFIDENCE_DEGRADE_THRESHOLD:
                logger.info(
                    "意图降级 %s→CLARIFICATION (confidence=%.2f < %.1f, q=%r)",
                    output.intent, output.confidence, CONFIDENCE_DEGRADE_THRESHOLD, question[:50],
                )
                return IntentOutput(
                    intent="CLARIFICATION",
                    normalized_question=output.normalized_question,
                    confidence=output.confidence,
                    reason=f"置信度过低 ({output.confidence:.2f}), 降级为追问",
                    chart_type_hint=output.chart_type_hint,
                )

            return output

        except (json.JSONDecodeError, ValidationError, KeyError, ValueError) as e:
            # schema 不匹配 → 重试
            last_error = e
            logger.warning("意图识别 schema 不匹配 (attempt %d): %s", attempt + 1, e)
            continue
        except Exception as e:
            # LLM 调用失败 → 直接降级, 不重试 (网络问题重试无用)
            logger.warning("意图识别 LLM 调用失败, 降级 CLARIFICATION: %s", e)
            return IntentOutput(
                intent="CLARIFICATION",
                normalized_question=question,
                confidence=0.0,
                reason=f"LLM 调用失败: {e}",
            )

    # 重试耗尽 → 降级
    logger.warning("意图识别重试 %d 次仍失败, 降级 CLARIFICATION: %s", MAX_RETRIES, last_error)
    return IntentOutput(
        intent="CLARIFICATION",
        normalized_question=question,
        confidence=0.0,
        reason=f"重试 {MAX_RETRIES} 次后仍无法识别意图",
    )
