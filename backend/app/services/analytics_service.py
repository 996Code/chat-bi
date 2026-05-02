"""埋点分析服务 — 追踪关键用户行为。"""
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent
from app.core.logging import get_logger

logger = get_logger(__name__)

# 定义的事件常量
EVENT_USER_LOGIN = "user_login"
EVENT_USER_LOGOUT = "user_logout"
EVENT_DATASOURCE_CREATE = "datasource_create"
EVENT_DATASOURCE_TEST = "datasource_test"
EVENT_DATASOURCE_SCAN = "datasource_scan"
EVENT_QUERY_EXECUTE = "query_execute"
EVENT_QUERY_SUCCESS = "query_success"
EVENT_QUERY_ERROR = "query_error"
EVENT_QUERY_SAVE = "query_save"
EVENT_QUERY_EXPORT_CSV = "query_export_csv"
EVENT_CHART_VIEW = "chart_view"
EVENT_CHART_TYPE_CHANGE = "chart_type_change"
EVENT_FEEDBACK_SUBMIT = "feedback_submit"
EVENT_FIRST_USE_COMPLETE = "first_use_complete"

ALL_EVENTS = {
    EVENT_USER_LOGIN, EVENT_USER_LOGOUT,
    EVENT_DATASOURCE_CREATE, EVENT_DATASOURCE_TEST, EVENT_DATASOURCE_SCAN,
    EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR,
    EVENT_QUERY_SAVE, EVENT_QUERY_EXPORT_CSV,
    EVENT_CHART_VIEW, EVENT_CHART_TYPE_CHANGE,
    EVENT_FEEDBACK_SUBMIT, EVENT_FIRST_USE_COMPLETE,
}


async def track_event(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    event_name: str,
    event_data: dict[str, Any] | None = None,
) -> None:
    """记录一个分析事件。"""
    import json

    event = AnalyticsEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        event_name=event_name,
        event_data=json.dumps(event_data or {}, ensure_ascii=False),
    )
    db.add(event)


async def get_user_events(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """获取用户的事件历史。"""
    from sqlalchemy import select

    result = await db.execute(
        select(AnalyticsEvent)
        .where(
            AnalyticsEvent.tenant_id == tenant_id,
            AnalyticsEvent.user_id == user_id,
        )
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "event_name": e.event_name,
            "event_data": e.event_data,
            "created_at": str(e.created_at),
        }
        for e in events
    ]
