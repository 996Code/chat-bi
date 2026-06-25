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
    normalized_question: str = Field(description="剥离可视化措辞后的纯净问题")
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
- TEXT_TO_SQL: 查询数据 (如"本月销售额"、"各品类销量排名")
- CLARIFICATION: 模糊追问, 需上下文补全 (如"那个呢"、"上个月呢")
- GENERAL: 闲聊/打招呼, 不涉及数据查询 (如"你好"、"谢谢")
- CHART_MODIFY: 只修改图表展示方式, 不改数据 (如"换成饼图"、"用柱状图")
- EXPLANATION: 解释已有 SQL 或结果 (如"这个查询什么意思"、"为什么是这个数")

规则:
1. normalized_question: 剥离可视化措辞后的纯净业务问题 ("用折线图展示本月销售" → "本月销售")
2. chart_type_hint: 如果含可视化措辞, 提取图表类型 (line/bar/pie/scatter), 否则 null
3. confidence: 你对这个意图判断的置信度 (0-1)

只返回 JSON, 格式: {"intent": "...", "normalized_question": "...", "confidence": 0.9, "reason": "...", "chart_type_hint": "line"}
"""


async def classify_intent(question: str, llm_client, history: str | None = None) -> IntentOutput:
    """调 LLM 识别意图, 失败/低置信度降级 CLARIFICATION。

    Args:
        question: 用户原始问题
        llm_client: AsyncOpenAI client
        history: 多轮对话历史文本 (追问时注入, 让"再查一遍/上个月呢"能正确消解指代
                 并判为 TEXT_TO_SQL 而非 CLARIFICATION; 对标 ARC-04)

    Returns:
        IntentOutput — 始终返回 (不抛), 低置信/失败降级 CLARIFICATION
    """
    from app.core.config import get_settings
    from app.core.text_sanitize import sanitize_text
    settings = get_settings()

    # SEC-007: 用户输入进 LLM 前清洗 (NFKC + 去零宽/方向控制字符)
    clean_question = sanitize_text(question)
    # 追问时把历史拼进 user message (让意图识别结合上下文判断)
    user_content = clean_question
    if history:
        user_content = f"【对话历史】\n{history}\n\n【当前问题】{clean_question}"

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": _INTENT_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=300,
                temperature=0.0,
            )
            content = resp.choices[0].message.content or ""
            # OBS-002: 记录 token + prompt (请求级累加, T049 trace / T050 dump-prompts)
            from app.core.token_tracker import track_usage
            from app.core.prompt_capture import record_prompt
            track_usage(getattr(resp, "usage", None))
            record_prompt("intent", _INTENT_PROMPT, user_content, getattr(resp, "usage", None))
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

        except (json.JSONDecodeError, ValidationError, KeyError) as e:
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
