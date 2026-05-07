"""Shared AI pipeline utilities — decoupled helpers used by multiple nodes."""
import json

from langchain_openai import ChatOpenAI

from app.core.config import settings

# Shared extra_body for disabling LLM reasoning/thinking mode.
# Compatible with Mimo API (thinking.type) and other providers (enable_thinking).
LLM_NO_THINKING = {"enable_thinking": False, "thinking": {"type": "disabled"}}


def get_llm(
    max_tokens: int | None = None,
    temperature: float | None = None,
    model: str | None = None,
    response_format: dict | None = None,
) -> ChatOpenAI:
    """Create a ChatOpenAI instance with thinking disabled.

    Defaults to generation settings; override via kwargs.
    """
    kwargs: dict = dict(
        model=model or settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=temperature if temperature is not None else settings.llm_generation_temperature,
        max_tokens=max_tokens or settings.llm_generation_max_tokens,
        extra_body=LLM_NO_THINKING,
    )
    if response_format:
        kwargs["response_format"] = response_format
    return ChatOpenAI(**kwargs)


def append_all_table_names(schema_context: str, raw_metadata: str) -> str:
    """在 schema_context 末尾附加完整表名列表，防止 LLM 捏造表名。"""
    try:
        metadata = json.loads(raw_metadata)
        all_tables = [m["name"] for m in metadata.get("models", []) if m.get("name")]
    except (json.JSONDecodeError, TypeError, KeyError):
        return schema_context

    if not all_tables:
        return schema_context

    table_list = ", ".join(all_tables)
    footer = (
        f"\n注意：以下是数据库中所有可用的表名，"
        f"SQL 中引用的表名必须严格使用以下名称之一：\n"
        f"可用表名: {table_list}"
    )
    return schema_context + footer
