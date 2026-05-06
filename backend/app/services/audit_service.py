"""Audit log service for tracking user actions."""
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.sql import Select

from app.db.models import AuditLog
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def log_action(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    details: str = "",
    sql_text: str | None = None,
    result_count: int | None = None,
    execution_time_ms: int | None = None,
    sql_execution_time_ms: int | None = None,
    error_message: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """记录审计日志。慢查询自动标记。"""
    audit = AuditLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        sql_text=sql_text,
        result_count=result_count,
        execution_time_ms=execution_time_ms,
        sql_execution_time_ms=sql_execution_time_ms,
        error_message=error_message,
        conversation_id=conversation_id,
    )
    db.add(audit)

    # Slow query detection — use total pipeline time
    if execution_time_ms and execution_time_ms > settings.slow_query_threshold_ms:
        audit.is_slow = True
        audit.details = f"[SLOW:{execution_time_ms}ms] {details}" if details else f"[SLOW:{execution_time_ms}ms]"
        logger.warning("Slow query detected: %dms (SQL: %sms), action=%s, user=%s, sql=%s",
                       execution_time_ms, sql_execution_time_ms or "N/A", action, user_id, (sql_text or "")[:200])
    else:
        logger.info("Audit: %s %s %s by user %s (tenant %s)", action, resource_type, resource_id, user_id, tenant_id)


async def get_slow_queries(
    db: AsyncSession,
    tenant_id: str,
    limit: int = 50,
    hours: int = 24,
) -> list[dict]:
    """获取最近慢查询列表，含数据源名称/用户问题/对话追溯。"""
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(hours=hours)
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.execution_time_ms > settings.slow_query_threshold_ms,
            AuditLog.created_at >= cutoff,
        )
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    # Get datasource names for all resource_ids
    from app.db.models import DataSource
    datasource_ids = set()
    for log in logs:
        if log.resource_id:
            datasource_ids.add(log.resource_id)

    datasource_map = {}
    if datasource_ids:
        ds_result = await db.execute(
            select(DataSource).where(DataSource.id.in_(datasource_ids))
        )
        for ds in ds_result.scalars().all():
            datasource_map[str(ds.id)] = ds.name

    return [
        {
            "id": str(log.id),
            "datasource_id": log.resource_id or "",
            "datasource_name": datasource_map.get(log.resource_id or "", "—"),
            "action": log.action,
            "user_question": _extract_question(log.details or ""),
            "sql_text": log.sql_text,
            "total_time_ms": log.execution_time_ms,
            "sql_time_ms": log.sql_execution_time_ms,
            "error_message": log.error_message,
            "details": log.details,
            "conversation_id": log.conversation_id,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


def _extract_question(details: str) -> str:
    """从 details 中提取用户问题。"""
    # Format: "question=... intent=..."
    idx = details.find("question=")
    if idx == -1:
        return ""
    start = idx + len("question=")
    intent_idx = details.find(" intent=", start)
    if intent_idx == -1:
        return details[start:]
    return details[start:intent_idx]


async def get_slow_query_stats(
    db: AsyncSession,
    tenant_id: str,
    hours: int = 24,
) -> dict:
    """获取慢查询统计。"""
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(hours=hours)
    stmt = (
        select(
            func.count().label("total"),
            func.avg(AuditLog.execution_time_ms).label("avg_ms"),
            func.max(AuditLog.execution_time_ms).label("max_ms"),
        )
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.execution_time_ms > settings.slow_query_threshold_ms,
            AuditLog.created_at >= cutoff,
        )
    )
    result = await db.execute(stmt)
    row = result.one()
    return {
        "total_slow_queries": row.total or 0,
        "avg_execution_time_ms": int(row.avg_ms or 0),
        "max_execution_time_ms": int(row.max_ms or 0),
        "threshold_ms": settings.slow_query_threshold_ms,
        "window_hours": hours,
    }
