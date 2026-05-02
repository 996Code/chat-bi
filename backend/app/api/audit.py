"""Audit log API for viewing user activity."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.db.session import get_db
from app.core.security import get_current_user
from app.db.models import AuditLog

router = APIRouter(prefix="/audit", tags=["审计日志"])


@router.get("", response_model=list[dict])
async def list_audit_logs(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
):
    """列出当前租户的审计日志。"""
    offset = (page - 1) * page_size

    query = select(AuditLog).where(
        AuditLog.tenant_id == user["tenant_id"]
    ).order_by(desc(AuditLog.created_at))

    if action:
        query = query.where(AuditLog.action == action)

    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    return [
        {
            "id": str(log.id),
            "tenant_id": str(log.tenant_id),
            "user_id": str(log.user_id),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details": log.details,
            "created_at": str(log.created_at),
        }
        for log in logs
    ]
