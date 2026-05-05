"""Analytics API — frontend event tracking and admin event listing."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.db.session import get_db
from app.db.models import AnalyticsEvent
from app.core.security import get_current_user, require_role


def _iso(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

router = APIRouter(prefix="/analytics", tags=["分析"])

_ANONYMOUS_USER_ID = "00000000-0000-0000-0000-000000000000"


@router.post("/event")
async def track_single_event(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """记录单个前端事件（允许匿名）。"""
    from app.services.analytics_service import track_event

    event_name = body.get("event_name") or body.get("event") or ""
    event_data = body.get("event_data") or body.get("properties") or {}

    if not event_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MISSING_EVENT", "message": "缺少 event_name 字段", "details": None},
        )

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        from app.core.security import verify_access_token
        payload = verify_access_token(auth.split(" ", 1)[1])
        if payload:
            user_id = str(payload.get("user_id", _ANONYMOUS_USER_ID))
            tenant_id = str(payload.get("tenant_id", ""))
        else:
            user_id = _ANONYMOUS_USER_ID
            tenant_id = ""
    else:
        user_id = _ANONYMOUS_USER_ID
        tenant_id = ""

    await track_event(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        event_name=event_name,
        event_data=event_data,
    )
    await db.commit()

    return {"ok": True}


@router.post("/batch")
async def track_batch_events(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """批量记录前端事件（允许匿名）。"""
    from app.services.analytics_service import track_event

    events = body.get("events", [])
    if not isinstance(events, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_BATCH", "message": "events 必须是数组", "details": None},
        )

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        from app.core.security import verify_access_token
        payload = verify_access_token(auth.split(" ", 1)[1])
        if payload:
            user_id = str(payload.get("user_id", _ANONYMOUS_USER_ID))
            tenant_id = str(payload.get("tenant_id", ""))
        else:
            user_id = _ANONYMOUS_USER_ID
            tenant_id = ""
    else:
        user_id = _ANONYMOUS_USER_ID
        tenant_id = ""

    count = 0
    for item in events:
        event_name = item.get("event_name") or item.get("event") or ""
        if not event_name:
            continue
        event_data = item.get("event_data") or item.get("properties") or {}
        await track_event(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            event_name=event_name,
            event_data=event_data,
        )
        count += 1

    await db.commit()

    return {"ok": True, "count": count}


@router.get("/events")
async def list_events(
    admin: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
    event: str | None = Query(None),
    user_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出分析事件（仅管理员，按租户隔离）。"""
    query = select(AnalyticsEvent).where(
        AnalyticsEvent.tenant_id == admin["tenant_id"]
    ).order_by(desc(AnalyticsEvent.created_at))

    if event:
        query = query.where(AnalyticsEvent.event_name == event)
    if user_id:
        query = query.where(AnalyticsEvent.user_id == user_id)

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    events = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "tenant_id": str(e.tenant_id),
            "user_id": str(e.user_id),
            "event_name": e.event_name,
            "event_data": e.event_data,
            "created_at": _iso(e.created_at),
        }
        for e in events
    ]
