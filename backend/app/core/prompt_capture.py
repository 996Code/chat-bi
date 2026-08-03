"""
Prompt 捕获层 (T050 dump-prompts) — 请求级 LLM prompt 文本收集

设计 (对标 token_tracker.py 同款 contextvar 模式):
  - 用 contextvar 按请求隔离 (多并发请求不串)
  - 各 AI 节点调 LLM 后, 把 system/user prompt + usage 记录到当前请求
  - 请求结束 (chat_stream complete 事件前) 读取, 通过 /conversations/{id}/trace 导出

安全 (fail-closed):
  - 仅 DEBUG 模式在 StateStore 持久化 prompt 文本 (prompt 可能含敏感 schema/数据)
  - 非持久化时内存级, 请求结束即 GC, 不落盘

对标 Claude Code: dump-prompts 导出能力。

数据流:
  chat_stream 开始 → start_prompt_capture() → 设置 contextvar
  ├─ intent 节点 → record_prompt("intent", system, user, usage)
  ├─ thinking 节点 → record_prompt("thinking", system, user, usage)
  ├─ ...
  └─ chat_stream 结束 → stop_prompt_capture() → 返回 dict → /trace 端点

为什么单独一个模块而非在 token_tracker 中记录:
  - prompt 文本可能很大 (含 schema 上下文), 默认不收集, 仅在调试时启用
  - 关注点分离: token_tracker 只关注数值统计, prompt_capture 关注文本记录
  - 安全: prompt 含敏感信息, 需要独立的安全控制 (DEBUG 模式才持久化)
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

# 请求级隔离 (async 安全)
# contextvars 保证每个 async 协程有自己的 prompt capture 实例
# 即使并发处理多个用户的请求, 数据也不会串
_current_capture: contextvars.ContextVar["PromptCapture | None"] = contextvars.ContextVar(
    "prompt_capture", default=None
)


@dataclass
class PromptRecord:
    """单次 LLM 调用的 prompt 记录。

    Attributes:
        node: 节点名 (intent/thinking/generate_sql/...)
        system: system message 内容 (可能含 schema 上下文)
        user: user message 内容 (用户问题的变体)
        prompt_tokens: 输入 token 数 (来自 LLM response.usage)
        completion_tokens: 输出 token 数
        total_tokens: 总 token 数
    """
    node: str                          # 节点名 (intent/thinking/generate_sql/...)
    system: str                        # system message
    user: str                          # user message
    prompt_tokens: int = 0             # 输入 token (来自 usage)
    completion_tokens: int = 0         # 输出 token
    total_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "system": self.system,
            "user": self.user,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class PromptCapture:
    """单次请求的 prompt 累积器。

    收集当前请求中所有 LLM 调用的 prompt 文本和 token 用量。
    每个请求一个 PromptCapture 实例, 通过 contextvars 隔离。
    """
    records: list[PromptRecord] = field(default_factory=list)

    def add(self, node: str, system: str, user: str, usage=None) -> None:
        """记录一次 LLM 调用的 prompt (usage 可为 None)。

        Args:
            node: AI 节点名
            system: system message 文本
            user: user message 文本
            usage: LLM response.usage 对象, 可为 None (LLM 调用失败时)
        """
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        tt = getattr(usage, "total_tokens", 0) if usage else 0
        self.records.append(
            PromptRecord(
                node=node, system=system, user=user,
                prompt_tokens=pt or 0, completion_tokens=ct or 0, total_tokens=tt or 0,
            )
        )

    def to_dict(self) -> dict:
        return {"records": [r.to_dict() for r in self.records]}

    @property
    def total_prompt_tokens(self) -> int:
        """所有记录的 prompt token 总数 (快速汇总, 不遍历 records 的 to_dict)。"""
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)


def start_prompt_capture() -> contextvars.Token:
    """开始一次请求的 prompt 捕获 (返回 contextvar token, 用于恢复)。

    在 chat_stream 开始时调用, 与 start_token_tracking 配合使用。
    返回的 contextvars.Token 用于 stop_prompt_capture 恢复 contextvar。
    """
    return _current_capture.set(PromptCapture())


def stop_prompt_capture(reset_token: contextvars.Token) -> dict:
    """结束捕获, 返回记录 dict, 恢复 contextvar。无捕获上下文返回空 dict。

    在 chat_stream 结束时 (SSE complete 事件前) 调用。
    返回的 dict 包含所有 LLM 调用的 prompt 记录。
    """
    capture = _current_capture.get()
    _current_capture.reset(reset_token)
    if capture is None:
        return {}
    return capture.to_dict()


def record_prompt(node: str, system: str, user: str, usage=None) -> None:
    """记录一次 LLM 调用的 prompt (各节点调用)。

    无捕获上下文时静默跳过 (不影响非 chat 场景, 如扫描/标题生成)。

    为什么静默跳过:
      - 扫描/标题生成等后台任务没有启动 prompt capture
      - 这些场景的 prompt 不需要导出到 /trace 端点
      - 如果抛异常, 会破坏非 chat 场景的正常流程

    性能: prompt 文本可能很大 (含 schema 上下文), 但只在 DEBUG 模式持久化。
    非 DEBUG 模式只在内存中保留, 请求结束即 GC, 不落盘。
    """
    capture = _current_capture.get()
    if capture is not None:
        capture.add(node, system, user, usage)
