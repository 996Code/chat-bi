"""Audit log API — admin access with filtering."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_

from app.db.session import get_db
from app.db.models import AuditLog
from app.core.security import require_role


from app.api._helpers import iso_format

router = APIRouter(prefix="/audit", tags=["审计"])


@router.get("/logs")
async def list_audit_logs(
    admin: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
    action: str | None = Query(None, description="按操作类型筛选"),
    user_id: str | None = Query(None, description="按用户 ID 筛选"),
    resource_type: str | None = Query(None, description="按资源类型筛选"),
    date_from: str | None = Query(None, description="起始日期 (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    cursor: str | None = Query(None, description="游标分页"),
    page_size: int = Query(50, ge=1, le=200),
):
    """查询审计日志（仅管理员，按租户隔离，支持多维度筛选）。"""
    tenant_id = admin["tenant_id"]
    conditions = [AuditLog.tenant_id == tenant_id]

    if action:
        conditions.append(AuditLog.action == action)
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if date_from:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to:
        conditions.append(AuditLog.created_at <= date_to + " 23:59:59")

    query = (
        select(AuditLog)
        .where(and_(*conditions))
        .order_by(desc(AuditLog.created_at))
        .limit(page_size + 1)
    )

    if cursor:
        query = query.where(AuditLog.created_at < cursor)

    result = await db.execute(query)
    logs = list(result.scalars().all())

    has_next = len(logs) > page_size
    if has_next:
        logs = logs[:page_size]

    next_cursor = str(logs[-1].created_at) if logs and has_next else None

    return {
        "data": [
            {
                "id": str(log.id),
                "tenant_id": str(log.tenant_id),
                "user_id": str(log.user_id),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "details": log.details,
                "sql_text": log.sql_text,
                "result_count": log.result_count,
                "execution_time_ms": log.execution_time_ms,
                "error_message": log.error_message,
                "created_at": iso_format(log.created_at),
            }
            for log in logs
        ],
        "next_cursor": next_cursor,
        "has_more": has_next,
    }
