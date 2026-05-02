"""Feedback API — 用户可以对查询结果点赞/踩。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import Feedback
from app.core.security import get_current_user

router = APIRouter(prefix="/feedback", tags=["反馈"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_feedback(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rating = data.get("rating")
    if rating not in ("up", "down"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_RATING", "评分必须是 up 或 down"),
        )

    fb = Feedback(
        tenant_id=user["tenant_id"],
        user_id=user["user_id"],
        query_id=data.get("query_id", ""),
        rating=rating,
        comment=data.get("comment", ""),
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)

    return {"id": str(fb.id), "rating": fb.rating}


@router.get("", response_model=list[dict])
async def list_feedback(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Feedback)
        .where(Feedback.tenant_id == user["tenant_id"])
        .order_by(Feedback.created_at.desc())
    )
    feedbacks = result.scalars().all()
    return [
        {
            "id": str(fb.id),
            "query_id": fb.query_id,
            "rating": fb.rating,
            "comment": fb.comment,
            "created_at": str(fb.created_at),
        }
        for fb in feedbacks
    ]
