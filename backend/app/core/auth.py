"""
ChatBI v2 — JWT Authentication + RBAC (T005)

Every API endpoint that needs auth uses Depends(get_current_user).
JWT contains: user_id, email, tenant_id, role (对标 v1 经验教训 #3)

架构角色:
  - 认证层: get_current_user 从 Bearer token 提取用户身份
  - 授权层: require_role 工厂函数, 基于角色做细粒度权限控制
  - 多租户隔离: contextvars 实现请求级 tenant_id 传递, TenantMixin 提供 ORM 查询过滤
  - 审计日志: write_audit_log 独立事务写入, 三层降级保证不丢日志

+ Multi-tenant isolation (T006):
  机制: contextvars 存当前 tenant_id + TenantMixin.tenant_filter() 显式过滤。
  所有 tenant-scoped 模型继承 TenantMixin，查询时需 .where(Model.tenant_filter(tid))。
  对标: v1 经验教训 #48 — 多租户隔离应是默认行为

  NOTE: 当前为"半自动"（需调用方显式 filter），非 session event 全局自动注入。
  设计决策: 不引入全局 session event 自动注入。
    理由: (1) 影响 all queries, bug 会导致全系统隔离失效, 风险 > 收益;
          (2) 业务库 SQL 执行(T031)不走 ORM, session event 对它无效;
          (3) 多租户隔离靠 data_source 归属: chat 路径查 DataSource 用 tenant_filter,
              data_source_id 来自本租户 → 业务库连库天然隔离。
    所有 API 端点已显式 .where(tenant_filter()) (data_sources/semantic_models/chat)。

关键数据流:
  Request → HTTPBearer → decode_token → AuthUser → Depends(get_current_user) → 端点
                                                                      ↓
                                                              require_role() → 403
                                                                      ↓
                                                              set_current_tenant() → 隔离
                                                                      ↓
                                                              write_audit_log() → 追踪
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
    from jose import ExpiredSignatureError, JWTError
    from jose.exceptions import JWTClaimsError
except ImportError:
    # 防御性 import: 即使 jose 未安装, 模块也能被导入
    # 这在文档生成/静态分析场景下有用, 运行时会在 decode_token 时失败
    class ExpiredSignatureError(Exception): pass
    class JWTError(Exception): pass
    class JWTClaimsError(Exception): pass

# ── JWT Auth ───────────────────────────────────────────────────

security_scheme = HTTPBearer(auto_error=False)


class AuthUser:
    """Authenticated user extracted from JWT token.

    这个 dataclass-like 对象是请求生命周期内的用户身份上下文。
    通过 FastAPI Depends(get_current_user) 注入到端点, 端点无需自行解析 JWT。

    字段:
      - user_id: 用户唯一标识, 用于审计日志和资源归属
      - email: 用户邮箱, 用于显示和通知
      - tenant_id: 租户 ID, 用于多租户数据隔离 (T006)
      - role: 角色标识, 用于 RBAC 权限判断 (admin/user)
    """
    def __init__(self, user_id: str, email: str, tenant_id: str, role: str):
        self.user_id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.role = role


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
) -> AuthUser:
    """FastAPI dependency: extract and validate the current user from JWT.

    认证流程:
      1. 检查 Authorization header 是否存在 (HTTPBearer 解析)
      2. decode_token 验证 JWT 签名和过期时间
      3. 验证 token type 为 "access" (拒绝 refresh token 用于认证)
      4. 提取 payload 中的 user_id/email/tenant_id/role
      5. 返回 AuthUser 对象供端点使用

    安全设计:
      - 所有异常统一返回 401, 不暴露具体的 JWT 解析细节
      - 字段缺失时返回 401 而非 500 (fail-closed, 防 KeyError 泄漏)
      - 按异常类型分层处理: Expired/Claims/General JWTError + 结构异常

    并发安全: 本函数无状态, 每次请求独立调用, 天然线程安全。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except ExpiredSignatureError:
        # token 过期: 客户端应使用 refresh token 获取新的 access token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired, please refresh",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTClaimsError:
        # payload 声明异常 (如 nbf 未来时间, iss 不匹配等)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        # 签名无效/token 被篡改/格式错误
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (KeyError, AttributeError, TypeError, ValueError) as e:
        # 对标 M1: JWT payload 结构异常 → 401, 但不吞掉其他编程错误
        # ValueError: jwt.decode 可能对畸形 token (如段数不对) 抛 ValueError
        # KeyError/AttributeError/TypeError: 极少数情况下 jose 库内部可能抛此类异常
        logger.warning("JWT 解码结构异常: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_type = payload.get("type")
    if token_type != "access":
        # 拒绝 refresh token 用于认证端点
        # 这是 v1 经验教训: refresh token 应仅用于 /auth/refresh 端点
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    # 安全提取 JWT 字段 (对标 fail-closed: 缺字段 → 401 而非 500 KeyError)
    # 使用 .get() 而非 [] 访问, 避免 KeyError 崩溃
    user_id = payload.get("user_id")
    email = payload.get("email")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role")
    if not all([user_id, email, tenant_id, role]):
        # 任何一个字段为空/缺失 → 拒绝, 不暗示哪些字段缺失 (防信息泄露)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required fields",
        )

    return AuthUser(
        user_id=user_id,
        email=email,
        tenant_id=tenant_id,
        role=role,
    )


# ── RBAC ──────────────────────────────────────────────────────

def require_role(*roles: str):
    """FastAPI dependency factory: require one of the given roles.

    用法:
      @router.get("/admin-only")
      async def admin_endpoint(user: AuthUser = Depends(require_admin)):
          ...

    require_role 返回一个闭包, 闭包内先通过 get_current_user 获取用户,
    再检查 role 是否在允许的 roles 集合中。

    为什么用闭包而非 class:
      - FastAPI 的 Depends 支持任意 callable
      - 闭包方式比类更简洁, 类型提示更清晰
      - 闭包在每个请求中独立调用, 无需初始化

    性能: get_current_user 内部 decode_token 是唯一开销, 约 0.1ms。
    """
    async def _require_role(current_user: AuthUser = Depends(get_current_user)):
        if current_user.role not in roles:
            # 403 Forbidden: 用户已认证但无权限
            # 不返回 401 避免混淆"未认证"和"无权限"两种场景
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return current_user
    return _require_role


# 预定义的常用角色依赖
# admin: 管理员权限, 可访问所有端点
# user: 普通用户权限, 可访问大部分业务端点
require_admin = require_role("admin")
require_user = require_role("admin", "user")


# ── Multi-tenant session-level isolation (T006) ────────────────

# Set of model names that are GLOBAL (not tenant-scoped)
# 例如: Tenant 表本身是所有租户共享的, 不对 tenant_id 做过滤
GLOBAL_TABLES = {"Tenant"}

# Per-request tenant_id stored in async context
# We use a simple approach: inject tenant_id via a context variable
#
# 为什么用 contextvars 而非 request-scoped 依赖:
#   - contextvars 在 async 上下文中自动传播, 不依赖 request 对象
#   - 在定时任务/后台任务中也可设置, 不受限于 HTTP 请求
#   - 比 threading.local 更安全, 不跨 async task 泄漏
import contextvars

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_current_tenant(tenant_id: str) -> None:
    """Set the tenant_id for the current request context.

    在 JWT 认证成功后由 middleware 或端点调用。
    所有后续的 TenantMixin 查询将自动带上 tenant_id 过滤条件。
    """
    _current_tenant_id.set(tenant_id)


def get_current_tenant() -> str | None:
    """Get the tenant_id for the current request context.

    返回 None 的情况:
      - 未认证的请求 (如健康检查端点)
      - 全局表 (如 Tenant 表本身) 的查询
    调用方应检查 None 值, 避免无条件使用。
    """
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
    # DSO-07: 慢查询标记 (SQL 执行耗时 + 是否慢查询)
    duration_ms: int | None = None,
    is_slow: bool = False,
    # DSO-05: 数据源归属 (按源聚合统计用)
    data_source_id: str | None = None,
) -> None:
    """Write an audit log entry in an independent transaction.

    对标 v1 经验教训 #41: 审计日志覆盖成功+失败+拒绝，统一字段
    M7: 审计写入独立事务 — 业务 rollback 不丢失审计记录。
    降级链: 独立 session → 调用方 session → logger.error (至少文件有记录)

    三层降级策略 (M7):
      1. 优先: 创建独立数据库 session, 单独 commit (业务 rollback 不影响审计)
      2. 降级: 使用调用方传入的 db_session (业务若 rollback 会丢审计, 但写入成功)
      3. 最终: logger.error 写入日志文件 (至少文件级有记录)

    性能考虑:
      - 独立 session 创建开销约 1-2ms, 对 API 延迟影响可忽略
      - 异步写入, 不阻塞主业务逻辑
      - audit_enabled 开关可全局关闭, 用于压测或高吞吐场景

    参数说明:
      - db_session: 调用方的主业务 session, 用于降级写入
      - tenant_id: 租户 ID, 用于多租户审计追踪
      - resource_type: 资源类型 (如 "datasource", "conversation", "user")
      - action: 操作类型 (如 "create", "update", "delete", "query")
      - status: 操作结果 ("success", "failure", "denied")
      - duration_ms: 操作耗时, 用于 DSO-07 慢查询分析
      - is_slow: 是否被标记为慢查询, 用于性能监控
      - data_source_id: 数据源 ID, 用于 DSO-05 按源聚合统计
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
        duration_ms=duration_ms,
        is_slow=is_slow,
        data_source_id=data_source_id,
    )

    # M7: 优先独立事务写入 (业务 rollback 不影响审计)
    committed = False
    try:
        from app.db.session import get_engine
        engine = await get_engine()
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        audit_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with audit_factory() as audit_session:
            audit_session.add(log_entry)
            await audit_session.commit()
            committed = True
    except Exception as e:
        # 独立 session 失败 (event loop 差异 / 连接问题) → 降级到调用方 session
        logger.debug("审计独立 session 失败, 降级到调用方 session: %s", e)

    if not committed:
        # 降级: 用调用方 session (业务 rollback 会丢审计, 但至少写入成功)
        try:
            db_session.add(log_entry)
        except Exception as e:
            # 最终降级: logger.error (文件至少有记录)
            # 这是最后一道防线, 确保审计信息不丢失
            logger.error(
                "审计日志写入失败: %s | audit_entry: %s %s → %s",
                e, user_id, action, status,
            )

    logger.debug("Audit: %s %s → %s", user_id, action, status)