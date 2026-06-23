"""
T028: ask_user — Agent 不确定时暂停问用户 (ask_user 即 tool)

对标:
  - 设计法则 #3: ask_user 是 tool, 不是特殊机制 (Claude Code §3.5)
  - proposal.md:120: 暂停触发 = Schema不确定 + 结果异常 (不是每次SQL执行前)
  - AEE-001 step9: Agent 发现不确定时调用 ask_user → 用户输入作 tool_result 恢复流

设计:
  - AskUserTool: 决定是否需要问用户 (基于触发条件)
  - 触发条件 (对标 proposal.md:120, 不是无脑问):
    1. SCHEMA_AMBIGUOUS: 检索召回多个候选表无法确定 (confidence 低)
    2. RESULT_AMBIGUOUS: 结果自检异常 (T033 检出问题但无法自动修复)
  - 不触发: 正常流程 (避免打扰, 对标 proposal "减少无用确认")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AskUserReason(str, Enum):
    """需要问用户的原因 (对标 proposal.md:120 触发条件)。"""
    SCHEMA_AMBIGUOUS = "SCHEMA_AMBIGUOUS"  # schema 不确定 (多候选表/低置信)
    RESULT_AMBIGUOUS = "RESULT_AMBIGUOUS"  # 结果异常无法自修复


@dataclass
class AskUserRequest:
    """向用户提问的请求 (对标 Claude Code AskUserQuestionTool)。"""
    reason: AskUserReason
    question: str  # 给用户看的问题
    options: list[str] | None = None  # 可选项 (如候选表名)


def should_ask_for_schema(retrieval_result) -> AskUserRequest | None:
    """检索后判断是否需要问用户 (schema 不确定)。

    触发 (对标 proposal.md:120):
      - 检索无召回 + 意图是 TEXT_TO_SQL → 问用户换问法
      - 检索返回多候选且都低分 → 问用户选哪个表

    Returns:
        AskUserRequest 或 None (None = 不需要问, 继续流程)
    """
    # 无召回 → 让用户换问法 (已在 retriever 返回 no_match_reason)
    if hasattr(retrieval_result, "no_match_reason") and retrieval_result.no_match_reason:
        if not retrieval_result.models:
            return AskUserRequest(
                reason=AskUserReason.SCHEMA_AMBIGUOUS,
                question=retrieval_result.no_match_reason,
            )

    # 多候选且最高分也偏低 → 不确定哪个表
    if hasattr(retrieval_result, "models") and len(retrieval_result.models) > 3:
        top_score = retrieval_result.models[0].get("score", 0) if retrieval_result.models else 0
        if top_score < 0.5:  # 最高分都低, 不确定
            candidates = [m.get("name", "") for m in retrieval_result.models[:5]]
            return AskUserRequest(
                reason=AskUserReason.SCHEMA_AMBIGUOUS,
                question="检索到多个可能相关的表, 请确认你想查哪个:",
                options=candidates,
            )

    return None


def should_ask_for_result(check_result) -> AskUserRequest | None:
    """结果自检后判断是否需要问用户 (结果异常)。

    触发 (对标 proposal.md:120):
      - 结果自检异常 (0行/笛卡尔积/全NULL) 且无法自动修复 → 问用户

    Returns:
        AskUserRequest 或 None
    """
    if check_result.ok:
        return None

    # 异常 → 询问用户 (这里只判断要不要问, 自动修复由 T025 主循环决定)
    return AskUserRequest(
        reason=AskUserReason.RESULT_AMBIGUOUS,
        question=f"查询结果可能异常: {check_result.reason}。{check_result.suggestion}",
    )
