"""
T043: 负面信号检测 — 连续点踩/关键词 → 触发反馈表单

对标:
  - spec: 用户表达不满时主动收集反馈 (不是被动等用户点)
  - 检测维度:
    1. 连续点踩 (同一用户连续 dislike 同类查询)
    2. 关键词 (结果含"不对/错了/错误"等负面词)
"""
from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback

# 负面关键词 (用户回复中含这些词 → 主动触发反馈)
_NEGATIVE_KEYWORDS = frozenset({
    "不对", "错了", "错误", "不正确", "有问题", "查错了",
    "为什么", "怎么回事", "不合理", "看不懂",
})

# 连续点踩阈值 (同一用户连续 N 次 dislike → 主动要反馈)
_CONSECUTIVE_DISLIKE_THRESHOLD = 2


def detect_negative_signal(text: str) -> bool:
    """检测文本是否含负面信号 (关键词匹配)。

    对标 spec: 用户回复含"不对/错了"等 → 主动触发反馈表单。
    """
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in _NEGATIVE_KEYWORDS)


async def check_consecutive_dislikes(
    tenant_id: str,
    user_id: str,
    db: AsyncSession,
) -> bool:
    """检查用户是否连续点踩 (达到阈值 → 主动要反馈)。

    对标 spec: 同一用户连续 N 次 dislike → 系统主动询问详细反馈。
    """
    stmt = (
        select(Feedback.feedback_type)
        .where(
            Feedback.tenant_filter(tenant_id),
            Feedback.user_id == user_id,
        )
        .order_by(Feedback.created_at.desc())
        .limit(_CONSECUTIVE_DISLIKE_THRESHOLD)
    )
    result = await db.execute(stmt)
    recent_types = [row[0] for row in result]
    # 最近 N 次都是 dislike → 触发
    return len(recent_types) >= _CONSECUTIVE_DISLIKE_THRESHOLD and all(t == "dislike" for t in recent_types)
