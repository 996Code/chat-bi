"""意图分类节点：关键词预过滤 + LLM 兜底。"""
import asyncio
import hashlib
import json

from langchain_openai import ChatOpenAI

from app.ai.nodes.shared_utils import get_llm, LLM_NO_THINKING
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

INTENT_SYSTEM = """你是一个意图分类器。只返回 JSON，不要任何额外文字。

判断用户问题是否属于数据查询类问题：
- DataQuery: 涉及数据查询、统计分析、报表、SQL生成
- Other: 问候、闲聊、与数据无关

返回格式严格为: {"intent": "DataQuery"} 或 {"intent": "Other"}"""

_OTHER_KEYWORDS = frozenset({
    "你好", "hello", "hi", "嗨", "谢谢", "再见", "拜拜",
    "早上好", "晚上好", "下午好", "在吗", "你是谁", "你能做什么",
    "help", "thanks", "thank you", "bye", "good morning", "good evening",
    "what can you do", "who are you",
})

_QUERY_KEYWORDS = frozenset({
    "多少", "几个", "排名", "统计", "平均", "总计", "总数", "占比",
    "汇总", "对比", "趋势", "分布", "排名", "Top", "top",
    "查询", "列出", "显示", "查看", "分组", "排序", "筛选",
    "销售额", "利润", "收入", "金额", "订单", "用户", "商品",
    "复购", "转化率", "留存", "库存", "退款", "退货",
    "销量", "销量额", "交易额", "gmv", "roi",
    "每月", "每天", "每年", "季度", "同比", "环比",
    "趋势", "变化", "对比",
    "count", "sum", "average", "group by", "order by",
    "total", "revenue", "sales", "how many", "how much",
    "show me", "list", "rank", "breakdown",
})


def _keyword_classify(q: str) -> str | None:
    words = set(q.split())
    lower_q = q.lower()

    if words & _OTHER_KEYWORDS:
        return "Other"

    chinese_chars = sum(1 for c in q if '一' <= c <= '鿿')
    if chinese_chars <= 2 and len(q.strip().split()) <= 1 and len(q) <= 4:
        return "Other"

    if lower_q.startswith(("帮我", "给我", "替我")) and not (words & _QUERY_KEYWORDS):
        return "Other"

    query_match_count = sum(1 for kw in _QUERY_KEYWORDS if kw in lower_q)
    if query_match_count >= 2:
        return "DataQuery"

    return None


def _keyword_fallback(q: str) -> str:
    result = _keyword_classify(q)
    if result is not None:
        return result
    return "DataQuery"


def _intent_cache_key(question: str) -> str:
    return f"intent:{hashlib.sha256(question.strip().lower().encode()).hexdigest()}"


async def classify_intent(question: str) -> str:
    q = question.strip().lower()

    # Try Redis cache
    try:
        redis = await get_redis()
        raw = await redis.get(_intent_cache_key(question))
        if raw:
            intent = json.loads(raw)["intent"]
            logger.info("Intent cache HIT: %s", intent)
            return intent
    except Exception:
        pass

    # Fast path: keyword classification
    local_result = _keyword_classify(q)
    if local_result is not None:
        logger.info("Intent (local): %s for '%s'", local_result, question[:50])
        await _intent_cache_set(question, local_result)
        return local_result

    # LLM path for ambiguous cases
    llm = get_llm(
        max_tokens=settings.llm_intent_max_tokens,
        temperature=settings.llm_intent_temperature,
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

    await _intent_cache_set(question, intent)
    logger.info("Intent (llm): %s for '%s'", intent, question[:50])
    return intent


async def _intent_cache_set(question: str, intent: str) -> None:
    try:
        redis = await get_redis()
        await redis.setex(_intent_cache_key(question), 300, json.dumps({"intent": intent}))
    except Exception:
        pass
