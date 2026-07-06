"""
回复生成器 — 为非 SQL 意图 (GENERAL/EXPLANATION) 生成自然语言回复

对标:
  - Claude Code §2: Agent 最终总要给用户一个文本回复 (永不空白)
  - fail-closed 根本模式: LLM 失败时降级为默认提示, 而非返回空

设计:
  - generate_reply: GENERAL/EXPLANATION 意图 → LLM 生成简短自然语言回复
  - 是将来"结果摘要"的统一出口 (TEXT_TO_SQL 成功后也可调, Phase 5+)
  - LLM 失败 → 降级默认文案 (安全降级, 对标 chart_agent 的规则推断降级)

Fail-Closed:
  - 任何异常都不抛, 始终返回一句非空字符串 (前端永不渲染空白)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# GENERAL/EXPLANATION 意图降级时的默认回复 (LLM 失败/超时的安全降级, 非硬编码 bug 修复)
_FALLBACK_REPLY = '我是 ChatBI 智能助手, 可以帮你用自然语言查询业务数据。请试试问「本月销售额」或「各品类销量排名」。'

_REPLY_PROMPT = """你是 ChatBI 智能助手, 一个基于自然语言的数据查询 (BI) 工具。

用户的问题不属于数据查询范畴 (已被判定为闲聊/说明类意图)。请友好、简短地回复用户。

规则:
1. 回复控制在 1-2 句话, 简洁有礼
2. 如果用户打招呼 (你好/你是谁), 自我介绍并引导用户提问数据相关问题
3. 如果用户问非数据问题, 礼貌说明你的能力范围 (自然语言查业务数据), 引导提问
4. 直接输出回复文字, 不要 markdown, 不要解释你在做什么

用户问题: {question}

回复:"""


async def generate_reply(
    question: str,
    intent: str,
) -> str:
    """为非 SQL 意图生成自然语言回复。

    对标 chart_agent/thinking 的降级模式: LLM 失败 → 降级默认文案。

    Args:
        question: 用户原始问题
        intent: 意图类型 (GENERAL / EXPLANATION / CHART_MODIFY 等)

    Returns:
        始终返回非空字符串 (fail-closed, 前端永不空白)
    """
    from app.core.llm_client import llm_chat
    from app.core.text_sanitize import sanitize_text

    clean_question = sanitize_text(question)
    prompt = _REPLY_PROMPT.format(question=clean_question)

    system_msg = "你是 ChatBI 智能助手, 友好简短地回复用户。"
    try:
        content, _ = await llm_chat(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            node="generate_reply",
            temperature=0.3,
        )
        reply = content.strip()
        if not reply:
            logger.warning("回复生成返回空, 降级默认文案")
            return _FALLBACK_REPLY
        return reply
    except Exception as e:
        logger.warning("回复生成 LLM 失败, 降级默认文案: %s", e)
        return _FALLBACK_REPLY
