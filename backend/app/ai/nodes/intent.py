from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

INTENT_SYSTEM = """你是一个查询意图分类器。判断用户的问题是否属于数据查询类问题。

分类标准：
- DataQuery: 问题涉及数据查询、统计分析、报表生成、SQL生成等，需要从数据库获取信息
- Other: 问候、闲聊、技术问题、系统操作指令、与数据无关的内容

只返回一个词：DataQuery 或 Other"""


async def classify_intent(question: str) -> str:
    # Heuristic shortcut: if question is very short and looks like greeting
    stripped = question.strip().lower()
    if stripped in ("你好", "hello", "hi", "嗨", "在吗", "在不在", "好", "谢谢", "再见"):
        logger.info("Intent shortcut: Other (greeting)")
        return "Other"

    llm = ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=0,
        max_tokens=10,
    )
    messages = [
        ("system", INTENT_SYSTEM),
        ("human", f"问题: {question}"),
    ]
    response = await llm.ainvoke(messages)
    intent = response.content.strip()
    # Extract first word in case LLM adds extra text
    intent = intent.split()[0] if intent.split() else "Other"
    logger.info("Intent classification: %s", intent)
    return intent
