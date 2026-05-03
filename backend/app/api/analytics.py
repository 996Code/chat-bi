"""Analytics API — frontend event tracking and admin event listing."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.db.session import get_db
from app.db.models import AnalyticsEvent

router = APIRouter(prefix="/analytics", tags=["分析"])

# Placeholder UUID for anonymous events
_ANONYMOUS_USER_ID = "00000000-0000-0000-0000-000000000000"


# --- Admin-only dependency ---

async def require_admin(user=Depends(lambda: None)) -> dict:
    """Require the current user to have the admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ADMIN_REQUIRED", "message": "需要管理员权限", "details": None},
        )
    return user


# We need to re-define this properly with actual auth:
from app.core.security import get_current_user
from app.db.models import User


async def get_current_admin_user(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify current user has admin role, return user dict."""
    if current_user.get("role") == "admin":
        return current_user
    # Double-check against DB in case token is stale
    user_id = current_user.get("sub")
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()
        if db_user and db_user.role == "admin":
            return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "ADMIN_REQUIRED", "message": "需要管理员权限", "details": None},
    )


# --- Endpoints ---

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

    # Try to get user from JWT (optional — anonymous allowed)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        from app.core.security import verify_access_token
        payload = verify_access_token(auth.split(" ", 1)[1])
        if payload:
            user_id = str(payload.get("sub", _ANONYMOUS_USER_ID))
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

    # Resolve user info once (optional auth)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        from app.core.security import verify_access_token
        payload = verify_access_token(auth.split(" ", 1)[1])
        if payload:
            user_id = str(payload.get("sub", _ANONYMOUS_USER_ID))
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
    admin: dict = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    event: str | None = Query(None),
    user_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出分析事件（仅管理员）。"""
    query = select(AnalyticsEvent).order_by(desc(AnalyticsEvent.created_at))

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
            "created_at": str(e.created_at),
        }
        for e in events
    ]
