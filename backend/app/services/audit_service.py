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
    )
    db.add(audit)
    # Don't commit here — let the caller commit with their transaction
    logger.info("Audit: %s %s %s by user %s (tenant %s)", action, resource_type, resource_id, user_id, tenant_id)
