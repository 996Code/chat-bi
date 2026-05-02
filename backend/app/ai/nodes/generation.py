from typing import Any
from langchain_openai import ChatOpenAI

from app.ai.prompts.query_prompt import SYSTEM_PROMPT, build_user_prompt
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=0,
        max_tokens=2000,
    )


async def generate_sql(
    question: str,
    schema_context: str,
) -> str:
    llm = get_llm()
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", build_user_prompt(question, schema_context)),
    ]
    response = await llm.ainvoke(messages)
    sql = response.content.strip()
    # Strip markdown code blocks if LLM wraps SQL in them
    if sql.startswith("```"):
        sql = sql.removeprefix("```sql").removeprefix("```").strip()
    if sql.endswith("```"):
        sql = sql.removesuffix("```").strip()
    logger.info("LLM generated SQL (first 200 chars): %s", sql[:200])
    return sql
