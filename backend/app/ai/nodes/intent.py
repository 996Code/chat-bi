"""意图分类节点：关键词预过滤 + LLM + 内存缓存。"""
import asyncio
import json
import time

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

INTENT_SYSTEM = """你是一个意图分类器。只返回 JSON，不要任何额外文字。

判断用户问题是否属于数据查询类问题：
- DataQuery: 涉及数据查询、统计分析、报表、SQL生成
- Other: 问候、闲聊、与数据无关

返回格式严格为: {"intent": "DataQuery"} 或 {"intent": "Other"}"""

# --- 闲聊/非查询关键词（命中 → Other） ---
_OTHER_KEYWORDS = frozenset({
    "你好", "hello", "hi", "嗨", "谢谢", "再见", "拜拜",
    "早上好", "晚上好", "下午好", "在吗", "你是谁", "你能做什么",
    "help", "thanks", "thank you", "bye", "good morning", "good evening",
    "what can you do", "who are you",
})

# --- 数据查询特征词（命中 → DataQuery，跳过 LLM） ---
_QUERY_KEYWORDS = frozenset({
    # 聚合动词
    "多少", "几个", "排名", "统计", "平均", "总计", "总数", "占比",
    "汇总", "对比", "趋势", "分布", "排名", "Top", "top",
    # SQL 关键词中文
    "查询", "列出", "显示", "查看", "分组", "排序", "筛选",
    # 业务指标
    "销售额", "利润", "收入", "金额", "订单", "用户", "商品",
    "复购", "转化率", "留存", "库存", "退款", "退货",
    "销量", "销量额", "交易额", "gmv", "roi",
    # 时间维度
    "每月", "每天", "每年", "季度", "同比", "环比",
    "趋势", "变化", "对比",
    # 英文
    "count", "sum", "average", "group by", "order by",
    "total", "revenue", "sales", "how many", "how much",
    "show me", "list", "rank", "breakdown",
})

# --- 指令类（命中 → Other） ---
_COMMAND_PATTERNS = frozenset({
    "帮我写", "帮我生成", "翻译", "解释", "什么是", "为什么",
    "怎么做", "如何", "说明", "描述",
})

# 内存缓存 + TTL
_INTENT_CACHE: dict[str, tuple[str, float]] = {}
_INTENT_CACHE_TTL = 300
_INTENT_CACHE_MAX = 500
_INTENT_CACHE_CLEANUP = 120
_last_cleanup = 0.0


def _keyword_classify(q: str) -> str | None:
    """本地关键词分类：返回 DataQuery/Other，None 表示不确定需 LLM。

    规则优先级：
    1. 极短输入（≤4 字符且无查询特征） → Other
    2. 闲聊词 → Other
    3. 指令类开头 + 非查询内容 → Other
    4. 查询特征词 ≥ 2 个 → DataQuery
    """
    words = set(q.split())
    lower_q = q.lower()

    # Rule 1: 闲聊词 → Other
    if words & _OTHER_KEYWORDS:
        return "Other"

    # Rule 2: 极短输入 → Other
    chinese_chars = sum(1 for c in q if '一' <= c <= '鿿')
    if chinese_chars <= 2 and len(q.strip().split()) <= 1 and len(q) <= 4:
        return "Other"

    # Rule 3: 纯指令无数据上下文 → Other
    if lower_q.startswith(("帮我", "给我", "替我")) and not (words & _QUERY_KEYWORDS):
        return "Other"

    # Rule 4: 查询特征 ≥ 2 → DataQuery
    query_match_count = sum(1 for kw in _QUERY_KEYWORDS if kw in lower_q)
    if query_match_count >= 2:
        return "DataQuery"

    # Not confident enough — fall through to LLM
    return None


def _keyword_fallback(q: str) -> str:
    """LLM 失败时的最终兜底。"""
    result = _keyword_classify(q)
    if result is not None:
        return result
    # Default to DataQuery (safer than mis-classifying a real query as Other)
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
    while len(_INTENT_CACHE) >= _INTENT_CACHE_MAX:
        oldest = min(_INTENT_CACHE, key=lambda k: _INTENT_CACHE[k][1])
        del _INTENT_CACHE[oldest]


async def classify_intent(question: str) -> str:
    q = question.strip().lower()

    _maybe_cleanup_cache()

    # Check cache
    if q in _INTENT_CACHE:
        cached, ts = _INTENT_CACHE[q]
        if time.time() - ts < _INTENT_CACHE_TTL:
            return cached
        del _INTENT_CACHE[q]

    # Fast path: local keyword classification
    local_result = _keyword_classify(q)
    if local_result is not None:
        _INTENT_CACHE[q] = (local_result, time.time())
        logger.info("Intent (local): %s for '%s'", local_result, question[:50])
        return local_result

    # LLM path for ambiguous cases
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
    logger.info("Intent (llm): %s for '%s'", intent, question[:50])
    return intent
