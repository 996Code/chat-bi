"""
Token 采集层 (OBS-002/003) — 请求级 LLM token 用量统计

设计:
  - 用 contextvar 按请求隔离 (多并发请求不串)
  - 各 AI 节点调 LLM 后, 把 response.usage 累加到当前请求的 TokenTracker
  - chat 主循环结束前读取统计, 通过 SSE complete 事件返回前端

对标 Claude Code: /context 命令的 token 预算展示。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

# 请求级隔离 (async 安全)
_current_tracker: contextvars.ContextVar["TokenTracker | None"] = contextvars.ContextVar(
    "token_tracker", default=None
)


@dataclass
class TokenTracker:
    """单次请求的 token 累加器。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def add(self, usage) -> None:
        """累加一次 LLM 调用的 usage (OpenAI CompletionUsage 或 None)。"""
        if usage is None:
            return
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        self.call_count += 1

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.call_count,
        }


def start_token_tracking() -> contextvars.Token:
    """开始一次请求的 token 追踪 (返回 contextvar token, 用于恢复)。"""
    return _current_tracker.set(TokenTracker())


def stop_token_tracking(reset_token: contextvars.Token) -> dict:
    """结束追踪, 返回累计统计 dict, 恢复 contextvar。"""
    tracker = _current_tracker.get()
    _current_tracker.reset(reset_token)
    if tracker is None:
        return {}
    return tracker.to_dict()


def track_usage(usage) -> None:
    """记录一次 LLM 调用的 usage (各节点调用)。

    无追踪上下文时静默跳过 (不影响非 chat 场景, 如扫描/标题生成)。
    """
    tracker = _current_tracker.get()
    if tracker is not None:
        tracker.add(usage)
