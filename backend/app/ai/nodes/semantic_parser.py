"""语义解析节点：LLM 将用户问题解析为结构化 JSON。"""
import asyncio
import json
import re

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SEMANTIC_SYSTEM = """你是一个语义解析器。分析用户问题，根据提供的数据库结构，输出结构化 JSON。

## 输出字段
{
  "intent": "simple|aggregation|breakdown|ranking|trend|comparison|filter",
  "metric": {"function": "SUM|AVG|COUNT|MAX|MIN", "column": "列名", "table": "表名"},
  "dimensions": [{"column": "列名", "table": "表名"}],
  "filters": [{"column": "列名", "table": "表名", "operator": "=|!=|>|<|>=|<=|IN|LIKE", "value": "值"}],
  "time_range": {"field": "列名", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "relative": "自然语言"},
  "sort": {"column": "列名或聚合表达式", "order": "ASC|DESC"},
  "limit": 整数或null
}

## intent 说明
- simple: 简单查询，如"有多少用户"
- aggregation: 聚合统计，如"总销售额"
- breakdown: 分组分布，如"各城市的订单数量"
- ranking: 排序 TopN，如"销售额Top10商品"
- trend: 时间趋势，如"月度销售趋势"
- comparison: 对比，如"对比不同支付方式的交易额"
- filter: 带过滤条件，如"退款订单的支付方式"

## 规则
1. 只输出 JSON，不要任何额外文字
2. 不确定的字段设为 null
3. 表名和列名必须严格来自提供的 schema
4. 过滤条件的 value 使用数据库实际值（如 status='refunded' 而非 '退款'）
5. limit 默认 null，"TopN" 类问题提取 N
"""


async def parse_semantics(question: str, schema_context: str) -> dict:
    """解析用户问题为结构化语义 JSON。

    返回空结构兜底：如果 LLM 失败，返回 {"intent": null}。
    """
    prompt = f"""数据库结构：
{schema_context}

问题：{question}

请输出结构化 JSON。"""

    messages = [
        ("system", SEMANTIC_SYSTEM),
        ("human", prompt),
    ]

    llm = ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=0.0,
        max_tokens=1000,
        response_format={"type": "json_object"},
        extra_body={"enable_thinking": False},
    )

    try:
        async with asyncio.timeout(15):
            response = await llm.ainvoke(messages)
            raw = response.content.strip()
            if not raw:
                raise ValueError("Empty response")
            # Strip markdown code blocks if present
            raw = re.sub(r'^```(?:\w+)?\s*', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'\s*```\s*$', '', raw)
            result = json.loads(raw)
    except Exception as e:
        logger.warning("Semantic parse failed: %s", e)
        return {}

    # Validate and normalize
    defaults = {
        "intent": None, "metric": None, "dimensions": [],
        "filters": [], "time_range": None, "sort": None, "limit": None,
    }
    for k, v in defaults.items():
        result.setdefault(k, v)

    logger.info("Semantic parse result: %s", json.dumps(result, ensure_ascii=False))
    return result
