"""SQL 解释节点：用自然语言描述生成的 SQL 做了什么。"""
from app.ai.nodes.shared_utils import get_llm
from app.core.logging import get_logger

logger = get_logger(__name__)

EXPLAIN_PROMPT = """用一句话解释以下 SQL 查询在做什么。要求：
- 不超过 50 个字
- 使用中文
- 说明查了什么表、做了什么聚合/过滤/排序
- 不要包含任何技术术语（如 GROUP BY, JOIN 等）
- 直接输出解释，不要加前缀

SQL: {sql}"""


async def explain_sql(sql: str) -> str:
    """生成 SQL 的自然语言解释。"""
    if not sql:
        return ""

    try:
        llm = get_llm()
        messages = [
            ("system", "你是 SQL 解释助手。只输出简短的中文解释。"),
            ("human", EXPLAIN_PROMPT.format(sql=sql[:500])),
        ]
        response = await llm.ainvoke(messages)
        explanation = response.content.strip()
        # Truncate if too long
        if len(explanation) > 100:
            explanation = explanation[:97] + "..."
        logger.info("SQL explanation: %s", explanation)
        return explanation
    except Exception as e:
        logger.warning("SQL explanation failed: %s", e)
        return ""
