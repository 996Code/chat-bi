"""
ChatBI v2 — JWT Authentication + RBAC (T005)

Every API endpoint that needs auth uses Depends(get_current_user).
JWT contains: user_id, email, tenant_id, role (对标 v1 经验教训 #3)

+ Multi-tenant isolation (T006):
  机制: contextvars 存当前 tenant_id + TenantMixin.tenant_filter() 显式过滤。
  所有 tenant-scoped 模型继承 TenantMixin，查询时需 .where(Model.tenant_filter(tid))。
  对标: v1 经验教训 #48 — 多租户隔离应是默认行为

  NOTE: 当前为"半自动"（需调用方显式 filter），非 session event 全局自动注入。
  全局 session event 自动注入待 T014 (CRUD API) 时补 —— 届时有真实查询场景验证。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token
from app.db.models import AuditLog, TenantMixin, User

logger = logging.getLogger(__name__)

try:
    from jose import ExpiredSignatureError, JWTClaimsError, JWTError
except ImportError:
    ExpiredSignatureError = Exception
    JWTClaimsError = Exception
    JWTError = Exception

# ── JWT Auth ───────────────────────────────────────────────────

security_scheme = HTTPBearer(auto_error=False)


class AuthUser:
    """Authenticated user extracted from JWT token."""
    def __init__(self, user_id: str, email: str, tenant_id: str, role: str):
        self.user_id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.role = role


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
) -> AuthUser:
    """FastAPI dependency: extract and validate the current user from JWT."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired, please refresh",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTClaimsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    return AuthUser(
        user_id=payload["user_id"],
        email=payload["email"],
        tenant_id=payload["tenant_id"],
        role=payload["role"],
    )


# ── RBAC ──────────────────────────────────────────────────────

def require_role(*roles: str):
    """FastAPI dependency factory: require one of the given roles."""
    async def _require_role(current_user: AuthUser = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return current_user
    return _require_role


require_admin = require_role("admin")
require_user = require_role("admin", "user")


# ── Multi-tenant session-level isolation (T006) ────────────────

# Set of model names that are GLOBAL (not tenant-scoped)
GLOBAL_TABLES = {"Tenant"}

# Per-request tenant_id stored in async context
# We use a simple approach: inject tenant_id via a context variable
import contextvars

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_current_tenant(tenant_id: str) -> None:
    """Set the tenant_id for the current request context."""
    _current_tenant_id.set(tenant_id)


def get_current_tenant() -> str | None:
    """Get the tenant_id for the current request context."""
    return _current_tenant_id.get()


# TenantMixin 现定义于 app.db.models (纯 ORM 基类，避免循环依赖)
# auth.py 通过 re-export 保持向后兼容
__all__ = ["TenantMixin"]


# ── Audit Helpers ──────────────────────────────────────────────

async def write_audit_log(
    db_session,
    tenant_id: str,
    user_id: str | None,
    resource_type: str,
    action: str,
    status: str,
    resource_id: str | None = None,
    detail: dict | None = None,
    sql_text: str | None = None,
    error_message: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write an audit log entry.

    对标 v1 经验教训 #41: 审计日志覆盖成功+失败+拒绝，统一字段
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.audit_enabled:
        return

    log_entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        status=status,
        detail=detail,
        sql_text=sql_text,
        error_message=error_message,
        ip_address=ip_address,
    )
    db_session.add(log_entry)
    # Don't commit here — let the caller commit as part of their transaction
    logger.debug("Audit: %s %s → %s", user_id, action, status)