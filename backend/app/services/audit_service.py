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
    error_message: str | None = None,
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
        error_message=error_message,
    )
    db.add(audit)

    # Slow query detection
    if execution_time_ms and execution_time_ms > settings.slow_query_threshold_ms:
        audit.is_slow = True
        audit.details = f"[SLOW:{execution_time_ms}ms] {details}" if details else f"[SLOW:{execution_time_ms}ms]"
        logger.warning("Slow query detected: %dms, action=%s, user=%s, sql=%s",
                       execution_time_ms, action, user_id, (sql_text or "")[:200])
    else:
        logger.info("Audit: %s %s %s by user %s (tenant %s)", action, resource_type, resource_id, user_id, tenant_id)


async def get_slow_queries(
    db: AsyncSession,
    tenant_id: str,
    limit: int = 50,
    hours: int = 24,
) -> list[dict]:
    """获取最近慢查询列表。"""
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(hours=hours)
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.execution_time_ms > settings.slow_query_threshold_ms,
            AuditLog.created_at >= cutoff,
        )
        .order_by(desc(AuditLog.execution_time_ms))
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "user_id": str(log.user_id),
            "action": log.action,
            "sql_text": log.sql_text,
            "execution_time_ms": log.execution_time_ms,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


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
