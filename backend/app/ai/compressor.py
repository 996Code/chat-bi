"""
T038: 上下文压缩 — 压缩 + 状态补偿 + 熔断器

对标:
  - Claude Code §6.1: 压缩不是截断, 是"压缩 + 状态补偿"
  - §6.3: 压缩后面貌 = [摘要] + [最近3轮] + [状态补偿]
  - §6.4: 熔断器 (连续失败停止)
  - CMP-001~004 spec

设计:
  - estimate_tokens: 粗估 token (不依赖 tokenizer, ~4字符/token)
  - should_compress: token > 70% 模型上限 → True
  - compact_history: 旧轮次 → LLM 摘要, 保留最近 keep_recent 轮
  - 状态补偿: 压缩后从 StateStore (T036) 重注入 current_sql/filters/tables
  - CompressionCircuitBreaker: 连续3次失败熔断 (复用 sql_healer 熔断器模式)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """粗估 token 数 (不依赖 tiktoken, 省依赖)。

    近似: 英文 ~4 字符/token, 中文每字 ~1.5 token (中文字符密度高)。
    混合文本取中间值。够用于 70% 阈值判断 (不需要精确)。
    """
    if not text:
        return 0
    # 数中文字符 (每个 ~1.5 token)
    cjk_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    # 非中文字符 (~4 字符/token)
    other_chars = len(text) - cjk_count
    return int(cjk_count * 1.5 + other_chars / 4)


def _messages_token_count(messages: list[dict]) -> int:
    """估算 messages 总 token (content 拼接)。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        # role 开销 ~4 token
        total += 4
    return total


def should_compress(messages: list[dict], model_limit: int = 8192) -> bool:
    """token 超过模型上限的 70% → 触发压缩 (CMP-001)。

    Args:
        messages: 对话历史
        model_limit: 模型上下文上限 (config.llm_max_tokens 或模型实际窗口)
    """
    if not messages:
        return False
    from app.core.config import get_settings
    settings = get_settings()
    threshold = settings.compression_token_threshold  # 0.70
    current_tokens = _messages_token_count(messages)
    return current_tokens > model_limit * threshold


@dataclass
class CompactResult:
    """压缩结果。"""
    summary: str = ""  # 旧轮次的一句话摘要
    recent_messages: list[dict] = field(default_factory=list)  # 保留的最近 N 轮
    state_compensation: dict = field(default_factory=dict)  # 状态补偿 (sql/filters/tables)
    error: str | None = None  # 压缩失败时的错误


_COMPACT_PROMPT = """你是对话压缩器。将下面的对话历史压缩成一句话摘要, 保留关键信息 (查了什么表、什么指标、什么筛选条件、结果数字)。

对话历史:
{history}

只返回一句话摘要, 不要解释:"""


async def compact_history(
    messages: list[dict],
    keep_recent: int | None = None,
    llm_client=None,
) -> CompactResult:
    """压缩对话历史: 旧轮次 → 摘要, 保留最近 N 轮。

    对标 Claude Code §6.2: 旧轮次 LLM 生成摘要, 最近 N 轮保留原文。
    失败降级: LLM 失败 → 简单截断 + warning (不崩)。

    Args:
        messages: 完整对话历史
        keep_recent: 保留最近几轮 (None → config.compression_keep_recent_turns=3)
        llm_client: LLM client

    Returns:
        CompactResult — summary + recent_messages + (state_compensation 由调用方填充)
    """
    if keep_recent is None:
        from app.core.config import get_settings
        keep_recent = get_settings().compression_keep_recent_turns

    # 不够长 → 不压缩
    if len(messages) <= keep_recent * 2:  # 每轮 ~2 条 message (user+assistant)
        return CompactResult(recent_messages=messages)

    # 分割: 旧轮次 + 最近 N 轮
    split_at = len(messages) - keep_recent * 2
    old_messages = messages[:split_at]
    recent_messages = messages[split_at:]

    # LLM 生成摘要
    if llm_client is None:
        return CompactResult(recent_messages=recent_messages, error="无 LLM client")

    from app.core.config import get_settings
    settings = get_settings()
    history_text = "\n".join(
        f"{m['role']}: {m.get('content', '')[:200]}"  # 截断长内容
        for m in old_messages
    )

    try:
        resp = await llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": _COMPACT_PROMPT.format(history=history_text)}],
            max_tokens=200,
            temperature=0.0,
        )
        summary = (resp.choices[0].message.content or "").strip()
        logger.info("对话压缩成功: %d 轮 → 摘要 %d 字", len(old_messages), len(summary))
        return CompactResult(summary=summary, recent_messages=recent_messages)
    except Exception as e:
        logger.warning("对话压缩 LLM 失败, 降级截断: %s", e)
        # 降级: 无摘要, 只保留 recent (信息损失但可用)
        return CompactResult(recent_messages=recent_messages, error=str(e))


class CompressionCircuitBreaker:
    """压缩熔断器 (连续失败停止, 对标 Claude Code §6.4)。

    连续失败 >= threshold → 熔断 (停止压缩, 避免浪费 API)。
    成功 → 重置。有半开恢复 (和 SelfHealCircuitBreaker 一致)。
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: int = 120):
        import time
        self._threshold = threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._tripped_at: float = 0.0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            import time
            self._tripped_at = time.monotonic()
            logger.error("压缩熔断器触发: 连续 %d 次失败", self._consecutive_failures)

    def record_success(self) -> None:
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0
            self._tripped_at = 0.0

    def is_tripped(self) -> bool:
        if self._consecutive_failures < self._threshold:
            return False
        import time
        if self._tripped_at and (time.monotonic() - self._tripped_at) > self._cooldown_seconds:
            self._tripped_at = 0.0
            return False
        return True


# 模块级熔断器单例
_compression_cb: CompressionCircuitBreaker | None = None


def get_compression_circuit_breaker() -> CompressionCircuitBreaker:
    global _compression_cb
    if _compression_cb is None:
        from app.core.config import get_settings
        _compression_cb = CompressionCircuitBreaker(
            threshold=get_settings().compression_max_consecutive_failures,
        )
    return _compression_cb
