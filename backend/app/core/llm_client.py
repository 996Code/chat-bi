"""
T016-preC: LLM client 封装 (AsyncOpenAI) + 中文推断

对标: milvus_client.py / redis_client.py 的单例封装范式。
讯飞 MAAS 用 OpenAI 兼容协议 (base_url + api_key), 直接用 AsyncOpenAI 对接。

设计:
  - get_llm_client / get_embedding_client: 模块级单例, 配置来自 settings
  - infer_column_chinese: 调 LLM 批量补中文 display_name, 失败降级为空 dict
    (宁缺毋滥: LLM 挂了不阻塞扫描, 退化用列名即可)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_llm_client: Optional[AsyncOpenAI] = None
_embedding_client: Optional[AsyncOpenAI] = None


def get_llm_client() -> AsyncOpenAI:
    """对话/推断用的 LLM client 单例。"""
    global _llm_client
    if _llm_client is not None:
        return _llm_client
    settings = get_settings()
    _llm_client = AsyncOpenAI(
        base_url=settings.llm_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout,
    )
    return _llm_client


def get_embedding_client() -> AsyncOpenAI:
    """Embedding client 单例（可能与 LLM 是不同 endpoint/key）。"""
    global _embedding_client
    if _embedding_client is not None:
        return _embedding_client
    settings = get_settings()
    _embedding_client = AsyncOpenAI(
        base_url=settings.embedding_url,
        api_key=settings.embedding_api_key,
        timeout=settings.llm_timeout,
    )
    return _embedding_client


def extract_content(resp, default: str = "") -> str:
    """从 LLM 响应安全提取 content (防御 choices 为 None/空)。

    LLM 服务异常 (如 endpoint 不匹配) 可能返回空 choices,
    直接 resp.choices[0] 会抛 'NoneType' object is not subscriptable。
    本函数统一防御, 失败返回 default (宁缺毋滥)。
    """
    try:
        if resp and resp.choices:
            return resp.choices[0].message.content or default
    except (AttributeError, IndexError, TypeError) as e:
        logger.warning("extract_content: LLM 响应结构异常, 降级为空: %s", e)
    return default


async def llm_chat(
    messages: list[dict],
    *,
    node: str | None = None,
    temperature: float | None = None,
) -> tuple[str, object]:
    """统一 LLM 调用入口。

    model / max_tokens / api 配置全部走 settings，调用方不需要也不应该关心。
    temperature 是业务语义 (0.0 精确 / 0.3 自然)，由调用方控制。
    node 非空时自动记录 track_usage + record_prompt。

    Args:
        messages: OpenAI 格式消息列表。
        node: AI 节点名 (intent/thinking/generate_sql 等)，None 时不记录遥测。
        temperature: 生成温度，None 时用 settings.llm_temperature。

    Returns:
        (content, resp) — content 是 extract_content 结果，resp 是原始响应对象。
    """
    settings = get_settings()
    client = get_llm_client()
    temp = temperature if temperature is not None else settings.llm_temperature

    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        max_tokens=settings.llm_max_tokens,
        temperature=temp,
    )

    content = extract_content(resp)

    # 遥测: node 非空时自动记录 token 用量 + prompt 文本
    if node:
        from app.core.token_tracker import track_usage
        from app.core.prompt_capture import record_prompt

        usage = getattr(resp, "usage", None)
        track_usage(usage, node=node)

        # 从 messages 提取 system / user 文本 (record_prompt 需要)
        system_text = ""
        user_text = ""
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                system_text = msg.get("content", "")
            elif role == "user":
                user_text = msg.get("content", "")
        record_prompt(node, system_text, user_text, usage)

    return content, resp


def reset_clients() -> None:
    """重置单例（测试用）。"""
    global _llm_client, _embedding_client
    _llm_client = None
    _embedding_client = None


async def infer_column_chinese(table_name: str, columns: list[dict]) -> dict[str, str]:
    """调 LLM 批量推断列的中文 display_name。

    对标 v1 教训 #15: 元数据质量是准确率根本（中文描述为空 → LLM 只能猜）。
    本函数给"无注释的列"补中文语义。

    Args:
        table_name: 表名（给 LLM 上下文）
        columns: 列列表，每项含 name + 可选 data_type

    Returns:
        {列名: 中文名} 映射。失败/异常返回空 dict（降级，不抛）。
    """
    settings = get_settings()
    col_desc = ", ".join(
        f"{c['name']}({c.get('data_type', '')})" for c in columns
    )
    prompt = (
        f"你是数据库语义推断助手。表名: {table_name}\n"
        f"列: {col_desc}\n\n"
        f"为每个列推断一个简洁的中文展示名（display_name）。"
        f"只返回 JSON，格式: {{\"列名\": \"中文名\"}}，不要解释。"
    )

    try:
        content, _ = await llm_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_temperature,
        )
        from app.core.llm_json import parse_json_response
        result = parse_json_response(content)
        return result if isinstance(result, dict) else {}
    except Exception as e:
        logger.warning("infer_column_chinese: LLM 调用失败, 降级为空 dict: %s", e)
        return {}
