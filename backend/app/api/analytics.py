"""Analytics API — frontend event tracking and admin event listing."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.db.session import get_db
from app.db.models import AnalyticsEvent
from app.core.security import get_current_user, require_role


from app.api._helpers import iso_format

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
            "created_at": iso_format(e.created_at),
        }
        for e in events
    ]


@router.get("/slow-queries")
async def list_slow_queries(
    admin: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=200),
):
    """列出慢查询（仅管理员，按租户隔离）。"""
    from app.services.audit_service import get_slow_queries
    tenant_id = admin["tenant_id"]
    return await get_slow_queries(db, tenant_id, limit=limit, hours=hours)


@router.get("/slow-queries/stats")
async def get_slow_query_stats(
    admin: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
    hours: int = Query(24, ge=1, le=168),
):
    """获取慢查询统计（仅管理员）。"""
    from app.services.audit_service import get_slow_query_stats
    tenant_id = admin["tenant_id"]
    return await get_slow_query_stats(db, tenant_id, hours=hours)


@router.get("/cache-stats")
async def get_cache_stats(
    admin: dict = Depends(require_role("admin")),
):
    """获取缓存命中率统计（仅管理员）。"""
    from app.services.cache_service import cache_stats
    return cache_stats.snapshot()


@router.get("/pool-status")
async def get_pool_status(
    admin: dict = Depends(require_role("admin")),
):
    """获取连接池状态（仅管理员）。"""
    from app.services.connection_pool import pool_manager
    return await pool_manager.get_pool_status()
