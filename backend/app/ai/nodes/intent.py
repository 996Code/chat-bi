"""意图分类节点（Intent Classification Node）

本文件是 LangGraph AI 管道的第一站，负责判断用户输入是否为"数据查询"类问题。
在 graph.py 的 StateGraph 中，classify_intent 是入口节点（entry point），
它的输出决定后续走向：DataQuery → schema_selection，Other → misleading。

=== 三层分类策略（3-Layer Classification Strategy）===

    第 1 层：Redis 缓存（Cache）
        用问题的 SHA256 哈希作为 key，命中则直接返回，跳过所有计算。
        缓存 TTL = 300 秒（5 分钟），适合高频重复提问场景。

    第 2 层：关键词匹配（Keyword）
        用预定义的 frozenset 做集合交集运算，毫秒级返回。
        能处理明确的问候语和典型的数据查询，但无法理解语义。

    第 3 层：LLM 大模型（Large Language Model）
        对关键词无法判定的"模糊问题"，调用 LLM 做语义理解。
        有 5 秒超时保护，失败时回退到关键词兜底（_keyword_fallback）。

=== 与其他文件的关系 ===

    - graph.py：调用本文件的 classify_intent()，将结果写入 QueryState["intent"]
    - state.py（在 graph.py 中定义）：QueryState 是 LangGraph 的状态字典，
      intent 字段由本节点填充，route_by_intent() 据此做条件路由
    - shared_utils.py：提供 get_llm() 工厂函数，统一创建 ChatOpenAI 实例
    - redis_client.py：提供 get_redis() 异步连接，用于缓存读写
"""
import asyncio
import hashlib
import json

from langchain_openai import ChatOpenAI

from app.ai.nodes.shared_utils import get_llm, LLM_NO_THINKING
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# LLM 系统提示词（System Prompt）
# ---------------------------------------------------------------------------
# 告诉 LLM 它的角色是"意图分类器"，只返回 JSON，不要多余文字。
# response_format={"type": "json_object"} 会强制 LLM 输出合法 JSON，
# 但仍需在 prompt 中明确说明格式，双保险。
INTENT_SYSTEM = """你是一个意图分类器。只返回 JSON，不要任何额外文字。

判断用户问题是否属于数据查询类问题：
- DataQuery: 涉及数据查询、统计分析、报表、SQL生成
- Other: 问候、闲聊、与数据无关

返回格式严格为: {"intent": "DataQuery"} 或 {"intent": "Other"}"""

# ---------------------------------------------------------------------------
# 关键词集合
# ---------------------------------------------------------------------------
# frozenset vs set：
#   - set：可变集合，可以 add/remove 元素
#   - frozenset：不可变集合，创建后不能修改
#   这里用 frozenset 因为关键词是固定的，不需要运行时修改；
#   frozenset 还可以作为字典的 key（set 不行），且内存略省。
#   两者都支持 &（交集）、|（并集）等集合运算。
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
    """关键词快速分类 — 毫秒级返回，无需调用 LLM。

    参数:
        q: 用户输入的问题（已转小写、去首尾空格）

    返回值:
        "DataQuery" — 判定为数据查询
        "Other"    — 判定为非数据查询（问候/闲聊等）
        None       — 无法判定，需要交给 LLM 处理

    Python 语法提示:
        str | None 是 Python 3.10+ 的联合类型写法，
        等价于 Optional[str]（来自 typing 模块）。
    """
    # set(q.split()) 把问题按空格拆成单词集合
    # words & _OTHER_KEYWORDS 是集合交集运算（intersection），
    # 如果交集非空，说明问题中包含问候关键词
    words = set(q.split())
    lower_q = q.lower()

    if words & _OTHER_KEYWORDS:
        return "Other"

    # 短问题启发式：中文 ≤2 字 + 英文 ≤1 词 + 总长 ≤4 → 大概率是问候
    # '一' <= c <= '鿿' 判断字符是否在 CJK 统一汉字范围内
    chinese_chars = sum(1 for c in q if '一' <= c <= '鿿')
    if chinese_chars <= 2 and len(q.strip().split()) <= 1 and len(q) <= 4:
        return "Other"

    # "帮我/给我/替我" 开头但不含查询关键词 → 可能是闲聊
    # startswith() 接受元组参数，匹配元组中任一前缀
    if lower_q.startswith(("帮我", "给我", "替我")) and not (words & _QUERY_KEYWORDS):
        return "Other"

    # 统计问题中包含多少个查询关键词（子串匹配，不是整词）
    # kw in lower_q 检查关键词是否作为子串出现在问题中
    # >= 2 个匹配才判定为 DataQuery，避免单个词误判
    query_match_count = sum(1 for kw in _QUERY_KEYWORDS if kw in lower_q)
    if query_match_count >= 2:
        return "DataQuery"

    # 关键词无法判定，返回 None 让 LLM 接手
    return None


def _keyword_fallback(q: str) -> str:
    """关键词兜底分类 — LLM 失败时的最后防线。

    与 _keyword_classify 的区别：当关键词返回 None（无法判定）时，
    默认归为 "DataQuery"，宁可多查也不漏查（假阴性比假阳性代价更高）。
    """
    result = _keyword_classify(q)
    if result is not None:
        return result
    return "DataQuery"


def _intent_cache_key(question: str) -> str:
    """根据问题文本生成 Redis 缓存 key。

    构造方式: "intent:" + SHA256(问题去空格转小写)

    为什么用 SHA256 而不是直接用原文？
        1. 原文可能很长，Redis key 越短性能越好
        2. 原文可能含特殊字符/空格/换行，哈希后统一为十六进制字符串
        3. SHA256 碰撞概率极低，实际可忽略
    """
    return f"intent:{hashlib.sha256(question.strip().lower().encode()).hexdigest()}"


async def classify_intent(question: str) -> str:
    """意图分类主入口 — LangGraph 节点函数。

    在 graph.py 中被包装为 intent_node：
        async def intent_node(state: QueryState) -> dict:
            intent = await classify_intent(state["question"])
            return {"intent": intent}

    LangGraph 会把返回的 {"intent": "DataQuery"} 合并到 QueryState 中，
    然后 route_by_intent(state) 读取 state["intent"] 决定下一步走哪条边。

    参数:
        question: 用户原始输入的问题文本

    返回值:
        "DataQuery" 或 "Other"

    执行顺序: 缓存 → 关键词 → LLM → 兜底
    """
    q = question.strip().lower()

    # ---- 第 1 层：Redis 缓存 ----
    # try/except 包裹：Redis 不可用时不影响主流程，静默降级
    try:
        redis = await get_redis()  # await: 异步等待 Redis 连接
        raw = await redis.get(_intent_cache_key(question))
        if raw:
            # json.loads 把 JSON 字符串解析为 Python 字典
            intent = json.loads(raw)["intent"]
            logger.info("Intent cache HIT: %s", intent)
            return intent
    except Exception:
        pass  # 缓存失败不影响业务，继续走分类逻辑

    # ---- 第 2 层：关键词快速分类 ----
    local_result = _keyword_classify(q)
    if local_result is not None:
        logger.info("Intent (local): %s for '%s'", local_result, question[:50])
        await _intent_cache_set(question, local_result)
        return local_result

    # ---- 第 3 层：LLM 语义分类 ----
    # get_llm() 来自 shared_utils.py，统一创建 ChatOpenAI 实例
    # max_tokens/temperature 从 config.py 读取，避免硬编码
    # response_format={"type": "json_object"} 强制 LLM 输出合法 JSON
    llm = get_llm(
        max_tokens=settings.llm_intent_max_tokens,
        temperature=settings.llm_intent_temperature,
        response_format={"type": "json_object"},
    )
    # messages 是 LangChain 的消息格式：[(role, content), ...]
    # role 可以是 "system"（系统指令）、"human"（用户输入）、"ai"（模型回复）
    messages = [
        ("system", INTENT_SYSTEM),
        ("human", question),
    ]
    try:
        # asyncio.timeout(5): 5 秒超时保护，防止 LLM 响应过慢阻塞整个管道
        # async with 是异步上下文管理器，超时后自动取消 await 中的协程
        async with asyncio.timeout(5):
            response = await llm.ainvoke(messages)
            # ainvoke = async invoke，LangChain 的异步调用方法
            # 对比 invoke() 是同步版本，会阻塞当前线程
            raw = response.content.strip()
            if not raw:
                raise ValueError("Empty response from LLM")
            data = json.loads(raw)
            # dict.get("intent", "DataQuery"): 安全取值
            # 如果 "intent" key 不存在，返回默认值 "DataQuery" 而非抛 KeyError
            intent = data.get("intent", "DataQuery")
            # 防御性校验：LLM 可能返回非预期的值，强制归入合法范围
            if intent not in ("DataQuery", "Other"):
                intent = "DataQuery"
    except Exception as e:
        # LLM 调用失败（超时/网络错误/JSON 解析失败）→ 关键词兜底
        logger.warning("Intent LLM failed: %s", e)
        intent = _keyword_fallback(q)

    # 无论走 LLM 还是兜底，都写入缓存
    await _intent_cache_set(question, intent)
    logger.info("Intent (llm): %s for '%s'", intent, question[:50])
    return intent


async def _intent_cache_set(question: str, intent: str) -> None:
    """将意图分类结果写入 Redis 缓存。

    参数:
        question: 原始问题文本（用于生成缓存 key）
        intent:   分类结果 "DataQuery" 或 "Other"

    缓存策略:
        - TTL（Time To Live）= 300 秒（5 分钟）
          短 TTL 的原因：用户可能用相似措辞问不同意图的问题，
          长缓存会导致误判累积
        - 值为 JSON 字符串 {"intent": "DataQuery"}
          存 JSON 而非纯字符串，方便未来扩展（如加 confidence 字段）

    异常处理:
        try/except 静默吞掉所有异常 — Redis 不可用不应阻断主流程
    """
    try:
        redis = await get_redis()
        # setex = SET with EXpiry，原子操作：设置 key + 过期时间
        # 等价于 redis.set(key, value) + redis.expire(key, 300) 但只需一次网络往返
        await redis.setex(_intent_cache_key(question), 300, json.dumps({"intent": intent}))
    except Exception:
        pass
