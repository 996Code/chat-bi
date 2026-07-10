"""
T016-preC: LLM client 封装 (AsyncOpenAI) + 中文推断

对标: milvus_client.py / redis_client.py 的单例封装范式。
讯飞 MAAS 用 OpenAI 兼容协议 (base_url + api_key), 直接用 AsyncOpenAI 对接。

设计:
  - get_llm_client / get_embedding_client: 模块级单例, 配置来自 settings
  - llm_chat: 统一 LLM 调用入口, 含 retry/backoff (429/连接错误自动重试)
  - infer_column_chinese: 调 LLM 批量补中文 display_name, 失败降级为空 dict
    (宁缺毋滥: LLM 挂了不阻塞扫描, 退化用列名即可)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from openai import AsyncOpenAI, APIConnectionError, RateLimitError, APITimeoutError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# LLM 调用重试配置 (从 config 读取, 兼容单元测试直接引用)
def _get_retries():
    return get_settings().llm_max_retries

def _get_retry_delay():
    return get_settings().llm_retry_base_delay

_llm_client: Optional[AsyncOpenAI] = None
_embedding_client: Optional[AsyncOpenAI] = None


def get_llm_client() -> AsyncOpenAI:
    """对话/推断用的 LLM client 单例。"""
    global _llm_client
    if _llm_client is not None:
        return _llm_client
    settings = get_settings()
    # 对标 O4: 区分连接超时和读取超时 (单 600s 覆盖 connect+read 不合理)
    # connect: 建立 TCP 连接, 应短 (10s); read: 等响应, 可长 (推理模型需时间)
    import httpx
    _llm_client = AsyncOpenAI(
        base_url=settings.llm_url,
        api_key=settings.llm_api_key,
        timeout=httpx.Timeout(
            connect=settings.llm_connect_timeout,
            read=float(settings.llm_timeout),
            write=settings.llm_write_timeout,
            pool=settings.llm_pool_timeout,
        ),
    )
    return _llm_client


def get_embedding_client() -> AsyncOpenAI:
    """Embedding client 单例（可能与 LLM 是不同 endpoint/key）。"""
    global _embedding_client
    if _embedding_client is not None:
        return _embedding_client
    settings = get_settings()
    # 对标审计: 与 LLM client 同样区分 connect/read 超时
    import httpx
    _embedding_client = AsyncOpenAI(
        base_url=settings.embedding_url,
        api_key=settings.embedding_api_key,
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
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
    """统一 LLM 调用入口 (含 retry/backoff)。

    model / max_tokens / api 配置全部走 settings，调用方不需要也不应该关心。
    temperature 是业务语义 (0.0 精确 / 0.3 自然)，由调用方控制。
    node 非空时自动记录 track_usage + record_prompt。

    重试策略:
      - 429 RateLimitError: 指数退避重试 (最多 llm_max_retries 次)
      - APIConnectionError: 同上 (瞬态网络问题, 可重试恢复)
      - APITimeoutError: 不重试 (超时说明请求太大或服务太慢)
      - 其他错误: 不重试 ( BadRequest/Auth 等, 重试无意义)

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

    last_exc: Exception | None = None
    max_retries = _get_retries()
    retry_delay = _get_retry_delay()
    for attempt in range(max_retries + 1):
        try:
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                max_tokens=settings.llm_max_tokens,
                temperature=temp,
            )
            # 成功 — 处理遥测
            content = extract_content(resp)

            # 对标 O5: 检测 finish_reason="length" (输出被截断)
            # 截断时 content 可能不完整 (SQL 缺尾巴 / JSON 缺闭合括号), 后续处理会出错
            try:
                finish_reason = resp.choices[0].finish_reason
                if finish_reason == "length":
                    logger.warning(
                        "LLM 输出被截断 (finish_reason=length, max_tokens=%d), "
                        "结果可能不完整 — 考虑增大 LLM_MAX_TOKENS",
                        settings.llm_max_tokens,
                    )
            except (AttributeError, IndexError):
                pass  # mock/非标准响应无 finish_reason, 不阻塞

            if node:
                from app.core.token_tracker import track_usage
                from app.core.prompt_capture import record_prompt

                usage = getattr(resp, "usage", None)
                track_usage(usage, node=node)

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

        except RateLimitError as e:
            last_exc = e
            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt)
                logger.warning("LLM 429 限流, 第 %d 次重试 (等待 %.1fs): %s", attempt + 1, delay, str(e)[:80])
                await asyncio.sleep(delay)
            else:
                logger.error("LLM 429 限流, 重试 %d 次后放弃", max_retries)

        except APIConnectionError as e:
            last_exc = e
            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt)
                logger.warning("LLM 连接失败, 第 %d 次重试 (等待 %.1fs): %s", attempt + 1, delay, str(e)[:80])
                await asyncio.sleep(delay)
            else:
                logger.error("LLM 连接失败, 重试 %d 次后放弃", max_retries)

        except APITimeoutError as e:
            # 超时不重试 — 说明请求太大或服务太慢
            logger.warning("LLM 超时 (不重试): %s", str(e)[:80])
            raise

        except Exception as e:
            # 其他错误不重试 (BadRequest/Auth/等)
            raise

    # 所有重试耗尽
    if last_exc is not None:
        raise last_exc
    # 理论不可达: 循环要么 return, 要么 raise, 要么设置 last_exc
    raise RuntimeError("llm_chat: 重试循环异常退出 (last_exc 为 None)")


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
