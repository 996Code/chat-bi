"""
Token 采集层 (OBS-002/003) — 请求级 LLM token 用量统计

设计:
  - 用 contextvar 按请求隔离 (多并发请求不串)
  - 各 AI 节点调 LLM 后, 把 response.usage 累加到当前请求的 TokenTracker
  - chat 主循环结束前读取统计, 通过 SSE complete 事件返回前端

H5: 加 per-node 分段统计 (intent/thinking/generate_sql/heal_sql/generate_chart/generate_reply/compress)
对标 Claude Code /context 命令的 per-section token breakdown。

数据流:
  chat_stream 开始 → start_token_tracking() → 设置 contextvar
  ├─ intent 节点 → track_usage(usage, "intent")
  ├─ thinking 节点 → track_usage(usage, "thinking")
  ├─ generate_sql 节点 → track_usage(usage, "generate_sql")
  ├─ ...
  └─ chat_stream 结束 → stop_token_tracking() → 返回 dict → SSE complete 事件

并发安全:
  - contextvars 是 Python 3.7+ 原生 async 上下文隔离机制
  - 每个协程独立副本, 不共享, 无需锁

为什么 contextvars 而非 request 对象:
  - 定时任务/后台任务中也没有 request 对象
  - contextvars 不依赖框架, 可在任何 async 上下文中使用
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

# 请求级隔离 (async 安全)
# contextvars 保证每个 async 协程有自己的 token tracker 实例
# 即使并发处理多个用户的请求, 数据也不会串
_current_tracker: contextvars.ContextVar["TokenTracker | None"] = contextvars.ContextVar(
    "token_tracker", default=None
)


@dataclass
class NodeUsage:
    """单个 AI 节点的 token 用量。

    每个 AI 节点 (intent/thinking/generate_sql 等) 都有自己的 NodeUsage,
    记录该节点内部的多次 LLM 调用累计。

    Attributes:
        prompt_tokens: 输入 token 总数
        completion_tokens: 输出 token 总数
        total_tokens: 总 token 数 (prompt + completion)
        call_count: LLM 调用次数
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def add(self, usage) -> None:
        """累加一次 LLM 调用的 usage。

        usage 可能为 None (LLM 调用异常时), 此时静默跳过。
        使用 getattr 兼容各种 LLM 返回格式 (OpenAI/LiteLLM 等)。
        """
        if usage is None:
            return
        # getattr 兼容性: 不同 LLM 提供商的 usage 对象字段名可能不同
        # or 0 处理 None 值: 某些 provider 可能返回 None 而非 0
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        self.call_count += 1

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "call_count": self.call_count,
        }


@dataclass
class TokenTracker:
    """单次请求的 token 累加器 (含 per-node 分段)。

    H5: nodes 字段按 AI 节点名分桶, 支持 /context 级别的 per-section breakdown。

    总用量 = 所有节点用量之和 (nodes 中所有 NodeUsage 之和)
    分段用量 = nodes[node_name] 的 NodeUsage

    设计: 总用量和分段用量同时维护, 避免每次计算总和。
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    # H5: per-node 分段
    nodes: dict[str, NodeUsage] = field(default_factory=dict)

    def add(self, usage, node: str | None = None) -> None:
        """累加一次 LLM 调用的 usage (OpenAI CompletionUsage 或 None)。

        Args:
            usage: LLM response.usage 对象
            node: AI 节点名 (intent/thinking/generate_sql 等), None 时不分桶

        注意: usage 可能为 None (LLM 调用失败), 此时静默跳过。
        node 为 None 时只累加总用量, 不进入 per-node 分桶。
        """
        if usage is None:
            return
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        t = getattr(usage, "total_tokens", 0) or 0

        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        self.call_count += 1

        # H5: per-node 分桶
        if node:
            if node not in self.nodes:
                self.nodes[node] = NodeUsage()
            n = self.nodes[node]
            n.prompt_tokens += p
            n.completion_tokens += c
            n.total_tokens += t
            n.call_count += 1

    def to_dict(self) -> dict:
        result = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.call_count,
        }
        # H5: 附带 per-node 明细
        if self.nodes:
            result["nodes"] = {k: v.to_dict() for k, v in self.nodes.items()}
        return result


def start_token_tracking() -> contextvars.Token:
    """开始一次请求的 token 追踪 (返回 contextvar token, 用于恢复)。

    在 chat_stream 开始时调用。
    返回的 contextvars.Token 用于 stop_token_tracking 恢复 contextvar。
    """
    return _current_tracker.set(TokenTracker())


def stop_token_tracking(reset_token: contextvars.Token) -> dict:
    """结束追踪, 返回累计统计 dict, 恢复 contextvar。

    在 chat_stream 结束时 (SSE complete 事件前) 调用。
    返回的 dict 包含总用量和 per-node 分段统计。
    无追踪上下文时返回空 dict (不会崩溃)。
    """
    tracker = _current_tracker.get()
    _current_tracker.reset(reset_token)
    if tracker is None:
        return {}
    return tracker.to_dict()


def track_usage(usage, node: str | None = None) -> None:
    """记录一次 LLM 调用的 usage (各节点调用)。

    Args:
        usage: LLM response.usage 对象
        node: AI 节点名 (H5: per-node 分段统计)

    无追踪上下文时静默跳过 (不影响非 chat 场景, 如扫描/标题生成)。

    为什么静默跳过:
      - 扫描/标题生成等后台任务没有启动 token tracker
      - 这些场景的 token 用量不需要返回前端
      - 如果抛异常, 会破坏非 chat 场景的正常流程
    """
    tracker = _current_tracker.get()
    if tracker is not None:
        tracker.add(usage, node=node)


def get_node_usage(node: str) -> dict | None:
    """获取当前请求中某个 AI 节点的累计 token 用量。

    用于 SSE 中间事件附带该步骤的 LLM 调用信息, 让前端在 pipeline
    步骤上即时展示 "🤖 x1 · 1,234 tokens"。

    Args:
        node: AI 节点名 (intent/thinking/generate_sql 等)

    Returns:
        {"call_count": int, "total_tokens": int, ...} 或 None (无追踪上下文)
    """
    tracker = _current_tracker.get()
    if tracker is None or node not in tracker.nodes:
        return None
    return tracker.nodes[node].to_dict()
