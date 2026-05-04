"""意图分类节点：LLM + 关键词兜底 + 内存缓存。"""
import asyncio
import json
import time

from langchain_openai import ChatOpenAI

from app.ai.nodes.shared_utils import append_all_table_names
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

INTENT_SYSTEM = """你是一个意图分类器。只返回 JSON，不要任何额外文字。

判断用户问题是否属于数据查询类问题：
- DataQuery: 涉及数据查询、统计分析、报表、SQL生成
- Other: 问候、闲聊、与数据无关

返回格式严格为: {"intent": "DataQuery"} 或 {"intent": "Other"}"""

# 内存缓存 + TTL
_INTENT_CACHE: dict[str, tuple[str, float]] = {}
_INTENT_CACHE_TTL = 300  # 5 minutes
_INTENT_CACHE_MAX = 500  # memory cap
_INTENT_CACHE_CLEANUP = 120  # cleanup interval (seconds)
_last_cleanup = 0.0

# 闲聊关键词（兜底用）
_OTHER_KEYWORDS = frozenset({
    "你好", "hello", "hi", "嗨", "谢谢", "再见", "拜拜",
    "早上好", "晚上好", "下午好", "在吗", "你是谁", "你能做什么",
    "help", "thanks", "thank you", "bye", "good morning", "good evening",
    "what can you do", "who are you",
})


def _keyword_fallback(q: str) -> str:
    """关键词兜底：极短文本或匹配闲聊词 → Other。"""
    words = set(q.split())
    if words & _OTHER_KEYWORDS:
        return "Other"
    # 极短输入（<= 2 个中文字符或 1 个英文单词）视为闲聊
    chinese_chars = sum(1 for c in q if '一' <= c <= '鿿')
    english_words = len(q.strip().split())
    if chinese_chars <= 2 and english_words <= 1 and len(q) <= 4:
        return "Other"
    return "DataQuery"


def _maybe_cleanup_cache():
    """Lazy cleanup of expired cache entries."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _INTENT_CACHE_CLEANUP:
        return
    _last_cleanup = now
    expired = [k for k, (_, ts) in _INTENT_CACHE.items()
               if now - ts >= _INTENT_CACHE_TTL]
    for k in expired:
        del _INTENT_CACHE[k]
    # If still over capacity, evict oldest entries
    while len(_INTENT_CACHE) >= _INTENT_CACHE_MAX:
        oldest = min(_INTENT_CACHE, key=lambda k: _INTENT_CACHE[k][1])
        del _INTENT_CACHE[oldest]


async def classify_intent(question: str) -> str:
    q = question.strip().lower()

    # Lazy cleanup
    _maybe_cleanup_cache()

    # Check cache
    if q in _INTENT_CACHE:
        cached, ts = _INTENT_CACHE[q]
        if time.time() - ts < _INTENT_CACHE_TTL:
            return cached
        del _INTENT_CACHE[q]

    llm = ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=settings.llm_intent_temperature,
        max_tokens=settings.llm_intent_max_tokens,
        response_format={"type": "json_object"},
    )
    messages = [
        ("system", INTENT_SYSTEM),
        ("human", question),
    ]
    try:
        async with asyncio.timeout(5):
            response = await llm.ainvoke(messages)
            raw = response.content.strip()
            if not raw:
                raise ValueError("Empty response from LLM")
            data = json.loads(raw)
            intent = data.get("intent", "DataQuery")
            if intent not in ("DataQuery", "Other"):
                intent = "DataQuery"
    except Exception as e:
        logger.warning("Intent LLM failed: %s", e)
        intent = _keyword_fallback(q)

    _INTENT_CACHE[q] = (intent, time.time())
    logger.info("Intent: %s for '%s'", intent, question[:50])
    return intent
