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
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

# 请求级隔离 (async 安全)
_current_capture: contextvars.ContextVar["PromptCapture | None"] = contextvars.ContextVar(
    "prompt_capture", default=None
)


@dataclass
class PromptRecord:
    """单次 LLM 调用的 prompt 记录。"""
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
    """单次请求的 prompt 累积器。"""
    records: list[PromptRecord] = field(default_factory=list)

    def add(self, node: str, system: str, user: str, usage=None) -> None:
        """记录一次 LLM 调用的 prompt (usage 可为 None)。"""
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
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)


def start_prompt_capture() -> contextvars.Token:
    """开始一次请求的 prompt 捕获 (返回 contextvar token, 用于恢复)。"""
    return _current_capture.set(PromptCapture())


def stop_prompt_capture(reset_token: contextvars.Token) -> dict:
    """结束捕获, 返回记录 dict, 恢复 contextvar。无捕获上下文返回空 dict。"""
    capture = _current_capture.get()
    _current_capture.reset(reset_token)
    if capture is None:
        return {}
    return capture.to_dict()


def record_prompt(node: str, system: str, user: str, usage=None) -> None:
    """记录一次 LLM 调用的 prompt (各节点调用)。

    无捕获上下文时静默跳过 (不影响非 chat 场景, 如扫描/标题生成)。
    """
    capture = _current_capture.get()
    if capture is not None:
        capture.add(node, system, user, usage)
