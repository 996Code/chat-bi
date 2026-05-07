"""Query complexity estimator for model routing.

Used to decide whether a simple model path is sufficient or the full pipeline is needed.
Heuristics are keyword-based (no LLM calls) to keep routing fast.
"""


_SIMPLE_METRICS = {"数量", "总数", "合计", "总和", "平均", "最大", "最小", "列表", "明细"}
_SIMPLE_DIMENSIONS = {"按", "分组", "分组统计"}

_COMPLEX_KEYWORDS = {
    "同比", "环比", "排名", "排行", "top", "top10",
    "占比", "渗透率", "转化率", "漏斗",
    "连续", "趋势", "波动", "异常",
    "对比", "比较", "差异",
}

_AGG_KEYWORDS = {"sum", "count", "avg", "max", "min", "group", "order", "join"}
_TIME_KEYWORDS = {"月", "季度", "年", "周", "日", "上月", "今年", "去年", "同期", "最近"}


def estimate_query_complexity(question: str) -> dict:
    """Estimate query complexity for model routing.

    Returns: { level: 'simple' | 'normal' | 'complex', score: int, reasons: list }

    Scoring:
    - Simple (0-2): Single metric, single dimension, keywords like "数量", "总数", "列表"
    - Normal (3-5): Multiple dimensions, aggregation functions, time comparisons
    - Complex (6+): Multiple joins implied, nested subqueries, "同比/环比/排名", multi-step reasoning
    """
    q = question.lower().strip()
    score = 0
    reasons: list[str] = []

    # Single simple metric -> +1
    has_simple_metric = any(kw in question for kw in _SIMPLE_METRICS)
    if has_simple_metric:
        score += 1
        reasons.append("contains simple metric keyword")

    # Dimension/group keyword -> +1
    has_dimension = any(kw in question for kw in _SIMPLE_DIMENSIONS)
    if has_dimension:
        score += 1
        reasons.append("contains dimension/group keyword")

    # Time-related keyword -> +1
    has_time = any(kw in question for kw in _TIME_KEYWORDS)
    if has_time:
        score += 1
        reasons.append("contains time dimension keyword")

    # Complex keywords -> +3 each (joins, comparisons, rankings)
    found_complex = [kw for kw in _COMPLEX_KEYWORDS if kw in q]
    if found_complex:
        score += 3 * len(found_complex)
        reasons.append(f"complex keywords: {', '.join(found_complex)}")

    # Multiple aggregation hints -> +1 each
    agg_count = sum(1 for kw in _AGG_KEYWORDS if kw in q)
    if agg_count > 1:
        score += 1
        reasons.append(f"multiple aggregation hints ({agg_count})")

    # Question length as a rough proxy for multi-step reasoning
    char_count = len(question)
    if char_count > 50:
        score += 1
        reasons.append("long question (possible multi-step reasoning)")
    if char_count > 100:
        score += 1
        reasons.append("very long question (complex reasoning likely)")

    # Multiple question marks -> separate sub-questions
    question_marks = question.count("?") + question.count("？")
    if question_marks > 1:
        score += 2
        reasons.append(f"multiple sub-questions ({question_marks})")

    # Determine level
    if score <= 2:
        level = "simple"
    elif score <= 5:
        level = "normal"
    else:
        level = "complex"

    return {
        "level": level,
        "score": score,
        "reasons": reasons,
    }
