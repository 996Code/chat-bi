"""
T042/T044: 反馈系统 — 反馈收集 + 审核队列

对标:
  - T042: 用户反馈收集 (点赞/踩/改SQL/纠正图表/评论)
  - T044: admin 审核队列 (审核 → 回流知识库 or 拒绝)
  - v1 教训 #41: 反馈三态 (pending/approved/rejected)

API:
  POST /feedback              提交反馈 (T042)
  GET  /feedback              列表 (admin 看全部, 普通用户看自己的)
  GET  /feedback/pending      待审核队列 (admin only, T044)
  POST /feedback/{id}/review  审核反馈 (admin only, T044)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_admin, require_user, write_audit_log
from app.db.models import Feedback
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackCreate(BaseModel):
    """提交反馈 (T042)。"""
    feedback_type: str  # like / dislike / sql_correction / chart_correction / comment
    saved_query_id: str | None = None
    rating: int | None = None
    corrected_sql: str | None = None
    corrected_chart: dict | None = None
    comment: str | None = None


class FeedbackOut(BaseModel):
    id: str
    feedback_type: str
    rating: int | None = None
    corrected_sql: str | None = None
    comment: str | None = None
    is_reviewed: bool = False
    review_status: str | None = None


class ReviewRequest(BaseModel):
    """审核反馈 (T044)。"""
    status: str  # approved / rejected
    note: str | None = None


_VALID_FEEDBACK_TYPES = {"like", "dislike", "sql_correction", "chart_correction", "comment"}
_VALID_REVIEW_STATUS = {"approved", "rejected"}


@router.post("", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
async def create_feedback(
    body: FeedbackCreate,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """提交反馈 (T042: 点赞/踩/改SQL/纠正图表/评论)。"""
    if body.feedback_type not in _VALID_FEEDBACK_TYPES:
        raise HTTPException(status_code=422, detail=f"无效反馈类型: {body.feedback_type}")

    fb = Feedback(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        saved_query_id=body.saved_query_id,
        feedback_type=body.feedback_type,
        rating=body.rating,
        corrected_sql=body.corrected_sql,
        corrected_chart=body.corrected_chart,
        comment=body.comment,
        is_reviewed=False,
        review_status="pending",
    )
    db.add(fb)
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="feedback", action="create", status="success",
        resource_id=fb.id, detail={"type": body.feedback_type},
    )
    await db.commit()
    return FeedbackOut(**{k: getattr(fb, k) for k in FeedbackOut.model_fields})


@router.get("", response_model=list[FeedbackOut])
async def list_feedback(
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """列出反馈 (普通用户看自己的, admin 看全部)。"""
    stmt = select(Feedback).where(Feedback.tenant_filter(user.tenant_id))
    if user.role != "admin":
        stmt = stmt.where(Feedback.user_id == user.user_id)
    stmt = stmt.order_by(Feedback.created_at.desc())
    result = await db.execute(stmt)
    return [FeedbackOut(**{k: getattr(f, k) for k in FeedbackOut.model_fields}) for f in result.scalars()]


@router.get("/pending", response_model=list[FeedbackOut])
async def list_pending(
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """待审核队列 (T044, admin only)。"""
    stmt = (
        select(Feedback)
        .where(
            Feedback.tenant_filter(user.tenant_id),
            Feedback.is_reviewed == False,  # noqa: E712
        )
        .order_by(Feedback.created_at.desc())
    )
    result = await db.execute(stmt)
    return [FeedbackOut(**{k: getattr(f, k) for k in FeedbackOut.model_fields}) for f in result.scalars()]


@router.post("/{fb_id}/review", response_model=FeedbackOut)
async def review_feedback(
    fb_id: str,
    body: ReviewRequest,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """审核反馈 (T044: approved → 回流知识库, rejected → 拒绝)。"""
    if body.status not in _VALID_REVIEW_STATUS:
        raise HTTPException(status_code=422, detail=f"无效审核状态: {body.status}")

    stmt = select(Feedback).where(
        Feedback.id == fb_id,
        Feedback.tenant_filter(user.tenant_id),
    )
    fb = (await db.execute(stmt)).scalar_one_or_none()
    if fb is None:
        raise HTTPException(status_code=404, detail="反馈不存在")

    fb.is_reviewed = True
    fb.review_status = body.status
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="feedback", action="review", status="success",
        resource_id=fb.id, detail={"review_status": body.status, "note": body.note},
    )
    await db.commit()
    return FeedbackOut(**{k: getattr(fb, k) for k in FeedbackOut.model_fields})
