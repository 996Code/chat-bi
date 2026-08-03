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
    """需要问用户的原因 (对标 proposal.md:120 触发条件)。

    SCHEMA_AMBIGUOUS: schema 不确定时触发, 如检索到多个候选表但无法确定目标表。
    RESULT_AMBIGUOUS: 结果自检异常时触发, 如查询返回 0 行或笛卡尔积。

    设计原则: 只在这两种明确的情况下触发, 不无脑问用户。
    对标 proposal.md: "减少无用确认" — 正常流程不打扰用户。
    """
    SCHEMA_AMBIGUOUS = "SCHEMA_AMBIGUOUS"  # schema 不确定 (多候选表/低置信)
    RESULT_AMBIGUOUS = "RESULT_AMBIGUOUS"  # 结果异常无法自修复


@dataclass
class AskUserRequest:
    """向用户提问的请求 (对标 Claude Code AskUserQuestionTool)。

    数据流:
      1. 调度器 (agent.py) 调用 should_ask_for_schema / should_ask_for_result
      2. 返回 AskUserRequest 或 None
      3. None → 继续流程; AskUserRequest → 暂停, 问用户, 等待 tool_result
      4. 用户输入作为 tool_result 恢复流

    Attributes:
      reason: 触发原因 (SCHEMA_AMBIGUOUS / RESULT_AMBIGUOUS)
      question: 给用户看的问题 (自然语言, 包含上下文)
      options: 可选选项 (如候选表名列表), 方便用户快速选择
    """
    reason: AskUserReason
    question: str  # 给用户看的问题
    options: list[str] | None = None  # 可选项 (如候选表名)


def should_ask_for_schema(retrieval_result) -> AskUserRequest | None:
    """检索后判断是否需要问用户 (schema 不确定)。

    触发 (对标 proposal.md:120):
      - 检索无召回 + 意图是 TEXT_TO_SQL → 问用户换问法
      - 检索返回多候选且都低分 → 问用户选哪个表

    不触发 (正常流程):
      - 检索有明确匹配 (高置信度)
      - 检索无召回但意图不是 TEXT_TO_SQL (如闲聊/说明类)

    Returns:
        AskUserRequest 或 None (None = 不需要问, 继续流程)

    边界情况:
      - retrieval_result.no_match_reason 存在但 models 非空 → 说明有部分匹配,
        用 no_match_reason 提示用户 (表名可能不对, 但列名匹配了)
      - 多候选但最高分 > 0.5 → 视为高置信, 不打扰用户
    """
    # 无召回 → 让用户换问法 (已在 retriever 返回 no_match_reason)
    if hasattr(retrieval_result, "no_match_reason") and retrieval_result.no_match_reason:
        if not retrieval_result.models:
            return AskUserRequest(
                reason=AskUserReason.SCHEMA_AMBIGUOUS,
                question=retrieval_result.no_match_reason,
            )

    # 多候选且最高分也偏低 → 不确定哪个表
    # 阈值 0.5: 经验值, 低于 0.5 表示检索结果置信度不足
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

    不触发:
      - check_result.ok = True (结果正常, 直接展示)
      - 结果异常但自动修复成功 (由 T025 主循环决定, 不在此处判断)

    Returns:
        AskUserRequest 或 None

    数据流:
      check_result (T033 结果自检输出) → 判断 ok 标志 → 决定是否问用户
    """
    if check_result.ok:
        return None

    # 异常 → 询问用户 (这里只判断要不要问, 自动修复由 T025 主循环决定)
    # check_result.reason 和 check_result.suggestion 由 T033 自检器生成
    return AskUserRequest(
        reason=AskUserReason.RESULT_AMBIGUOUS,
        question=f"查询结果可能异常: {check_result.reason}。{check_result.suggestion}",
    )
