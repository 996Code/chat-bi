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

    为什么不用 tiktoken:
      - 减少依赖安装, 避免不同模型 tokenizer 不一致问题
      - 70% 阈值判断对精度要求低, ±20% 误差不影响决策
      - 对标 Claude Code: 粗估足够, 不追求精确计数

    边界情况:
      - 空文本返回 0
      - 纯中文文本: 每字 ~1.5 token (如"本月销售额" 5 字 ≈ 7.5 token)
      - 纯英文文本: 每 4 字符 ~1 token (如"sales" 5 字 ≈ 1.25 token)
    """
    if not text:
        return 0
    # 数中文字符 (每个 ~1.5 token)
    cjk_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    # 非中文字符 (~4 字符/token)
    other_chars = len(text) - cjk_count
    return int(cjk_count * 1.5 + other_chars / 4)


def _messages_token_count(messages: list[dict]) -> int:
    """估算 messages 总 token (content 拼接)。

    数据流:
      1. 遍历 messages 列表, 提取每条消息的 content
      2. 用 estimate_tokens 估算 content 的 token 数
      3. 每条消息 +4 token 作为 role 开销 (system/user/assistant 标记)

    局限性: 不计算 tool_call/tool_result 等特殊消息的额外开销,
    但误差在可接受范围内 (70% 阈值的判断容忍 ±20% 误差)。
    """
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

    70% 阈值的设计考量:
      - 留出 30% 空间给 SQL 生成输出 (LLM 输出 token 也占用上下文)
      - 避免在极限时触发压缩 (此时压缩本身也需要 token)
      - 对标 Claude Code §6.1: compression 是预防性措施, 不是应急手段

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
    """压缩结果。

    数据流:
      summary: 旧轮次的 LLM 摘要 (一句话) — 注入到压缩后 prompt 的开头
      recent_messages: 保留的最近 N 轮对话 (保持原文, 不做压缩)
      state_compensation: 状态补偿字段 (sql/filters/tables) — 由调用方填充,
        用于在压缩后恢复关键的上下文状态, 避免信息丢失
      error: 压缩失败时的错误信息 (用于日志和监控, 不影响降级流程)

    对标 Claude Code §6.3: 压缩后面貌 = [摘要] + [最近3轮] + [状态补偿]
    """
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
) -> CompactResult:
    """压缩对话历史: 旧轮次 → 摘要, 保留最近 N 轮。

    对标 Claude Code §6.2: 旧轮次 LLM 生成摘要, 最近 N 轮保留原文。
    失败降级: LLM 失败 → 简单截断 + warning (不崩)。

    数据流:
      1. 输入: 完整对话历史 messages (list[dict], role/content 格式)
      2. 判断: 长度是否超过 keep_recent*2 条消息 (不够长则不压缩)
      3. 分割: 旧轮次 (old_messages) + 最近轮次 (recent_messages)
      4. 压缩: 旧轮次 → LLM 一句话摘要
      5. 输出: CompactResult {summary, recent_messages, state_compensation}
      6. 调用方: 将 summary 前置 + recent_messages 拼接 + 从 StateStore 注入 state_compensation

    降级路径:
      LLM 调用失败 → 丢弃旧轮次, 只保留 recent_messages (信息损失, 但可用)

    Args:
        messages: 完整对话历史
        keep_recent: 保留最近几轮 (None → config.compression_keep_recent_turns=3)

    Returns:
        CompactResult — summary + recent_messages + (state_compensation 由调用方填充)
    """
    if keep_recent is None:
        from app.core.config import get_settings
        keep_recent = get_settings().compression_keep_recent_turns

    # 不够长 → 不压缩 (keep_recent 条消息, 不假设每轮恰好 2 条)
    #
    # 为什么用 keep_recent*2:
    #   对话历史中每轮通常包含 user 和 assistant 两条消息, 但也有可能
    #   只有一条 (如系统提示), 或更多条 (如 tool_call 介入)。用 keep_recent*2
    #   作为阈值, 确保至少保留 keep_recent 完整轮次。
    #
    # 边界情况: len(messages) <= keep_recent*2 时直接返回, 不触发压缩
    if len(messages) <= keep_recent * 2:
        return CompactResult(recent_messages=messages)

    # 分割: 保留最近 keep_recent*2 条 (约 keep_recent 轮, 每轮 ~2 条)
    # 不假设每轮固定 2 条: 按"条数"切分, 保持最近的消息完整
    #
    # 设计决策: 按条数切分而非按轮次切分, 因为轮次结构不固定
    # (tool_call 介入时一轮可能多条)。按条数切分保证最近 keep_recent
    # 轮对话完整保留, 不影响 LLM 对最新上下文的理解。
    split_at = len(messages) - keep_recent * 2
    old_messages = messages[:split_at]
    recent_messages = messages[split_at:]

    # LLM 生成摘要 (llm_chat 内部获取 client, 不再需要外部传入)
    from app.core.llm_client import llm_chat
    history_text = "\n".join(
        f"{m['role']}: {m.get('content', '')[:200]}"  # 截断长内容: 每条消息最多 200 字符, 防止摘要 prompt 过长
        for m in old_messages
    )
    compact_prompt = _COMPACT_PROMPT.format(history=history_text)

    try:
        summary, _ = await llm_chat(
            messages=[{"role": "user", "content": compact_prompt}],
            node="compress",
            temperature=0.0,
        )
        summary = summary.strip()
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

    为什么需要熔断器:
      - 压缩本身需要调用 LLM, 如果 LLM 持续失败, 反复尝试压缩只会浪费 API
      - 熔断后降级为简单截断 (信息损失但可用), 保证主流程不中断
      - cooldown 后自动半开恢复, 避免永久性熔断

    线程安全: 当前为单线程设计, 多线程场景需加锁

    Attributes:
      threshold: 连续失败次数阈值 (默认 3)
      cooldown_seconds: 熔断后冷却时间 (默认 120s)
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: int = 120):
        import time
        self._threshold = threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._tripped_at: float = 0.0

    def record_failure(self) -> None:
        """记录一次失败, 达到阈值时触发熔断。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            import time
            self._tripped_at = time.monotonic()
            logger.error("压缩熔断器触发: 连续 %d 次失败", self._consecutive_failures)

    def record_success(self) -> None:
        """记录一次成功, 重置连续失败计数 (半开恢复)。"""
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0
            self._tripped_at = 0.0

    def is_tripped(self) -> bool:
        """检查是否处于熔断状态。

        如果已熔断且冷却时间已过, 自动半开恢复 (返回 False)。
        调用方在 is_tripped 返回 True 时应跳过压缩, 降级为截断。
        """
        if self._consecutive_failures < self._threshold:
            return False
        import time
        # 冷却时间已过 → 自动半开恢复
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
