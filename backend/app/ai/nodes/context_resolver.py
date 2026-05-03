"""多轮对话上下文解析：解析追问中的代词/省略/相对时间。"""
import re
from app.core.logging import get_logger

logger = get_logger(__name__)

# Relative time patterns (Chinese)
RELATIVE_TIME = {
    "上个月": "上一个月",
    "这个月": "当前月",
    "上周": "上一周",
    "这周": "当前周",
    "昨天": "前一天",
    "今天": "当前日",
    "去年同期": "去年同期",
    "上月": "上一个月",
    "本月": "当前月",
}

# Pronoun/ellipsis patterns that indicate follow-up
FOLLOW_UP_PATTERNS = [
    r"^(那|然后|接着|再|呢|又|还|也)",  # conversational continuations
    r"^(换个|换一个|另外|除了)",  # alternative queries
    r"^(按|根据|排序|分组|过滤)",  # modification
    r"(呢|吧|吗|啊|哦)$",  # trailing particles
    r"^(那|那这个|那个|这些|那些)",  # pronoun references
]


def _resolve_relative_time(question: str, prev_question: str) -> str:
    """Resolve relative time references using previous question context."""
    for pattern, replacement in RELATIVE_TIME.items():
        if pattern in question:
            # Find the table/time context from previous question
            time_words = re.findall(r"(\d{4}年|\d{1,2}月|\d{1,2}日|去年|今年|当月)", prev_question)
            if time_words:
                question = question.replace(pattern, time_words[0] + "对应的" + replacement)
    return question


def resolve_context(question: str, history: list[dict]) -> str:
    """Resolve follow-up question context using conversation history.

    Returns the resolved question that can be understood independently.
    """
    if not history:
        return question

    stripped = question.strip().lower()

    # Check if this is a follow-up
    is_follow_up = any(re.search(pat, stripped) for pat in FOLLOW_UP_PATTERNS)

    if not is_follow_up and len(stripped) < 5:
        # Very short input is likely a follow-up like "呢？"
        is_follow_up = True

    if not is_follow_up:
        return question

    # Get the last meaningful turn (with a real question)
    prev_question = ""
    for h in reversed(history):
        if h.get("question") and h["question"].strip().lower() != stripped:
            prev_question = h["question"]
            break

    if not prev_question:
        return question

    # Resolve relative time
    resolved = _resolve_relative_time(stripped, prev_question)

    # Handle "呢？" / "那呢？" — inherit previous question's context
    if resolved.strip() in ("呢", "吧", "吗", "那呢", "那呢？", "那?", "那个呢"):
        # Find time/group dimension to swap
        time_words = ["去年", "今年", "上月", "本月", "上周", "本周", "昨天", "今天"]
        for tw in time_words:
            if tw in prev_question:
                # Look for a different time word in current question
                for other_tw in time_words:
                    if other_tw in resolved and other_tw != tw:
                        return prev_question.replace(tw, other_tw)
        # If no time swap found, return as-is with context hint
        return resolved

    # Handle "按X排序呢" / "按X分组" — modify previous query
    if re.match(r"^(按|根据)", resolved):
        return f"{prev_question}，{resolved}"

    # Handle "换个方式" / "除了X"
    if re.match(r"^(换个|另外|除了)", resolved):
        return f"{prev_question}，{resolved}"

    # Default: combine with previous context
    if not any(re.search(pat, resolved) for pat in FOLLOW_UP_PATTERNS):
        return resolved

    return f"{prev_question}，{resolved}"
