"""Audit log service for tracking user actions."""
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog
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
    """记录审计日志。"""
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
    logger.info("Audit: %s %s %s by user %s (tenant %s)", action, resource_type, resource_id, user_id, tenant_id)
