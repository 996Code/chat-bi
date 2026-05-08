"""
ChatBI 安全核心 — JWT 令牌 + 密码哈希 + FastAPI 鉴权依赖

本文件提供三大安全能力：
1. 密码哈希（bcrypt）：注册时哈希存储，登录时验证，不可逆
2. JWT 令牌（python-jose）：签发/验证 access_token 和 refresh_token
3. FastAPI 依赖注入（Depends）：get_current_user / require_role 作为路由守卫

额外功能：
- 密码重置令牌（itsdangerous）：30 分钟有效，URL 安全的签名令牌
- 邮箱验证令牌（itsdangerous）：24 小时有效，同上

关键概念：
- JWT（JSON Web Token）：无状态认证，服务端不存储会话，令牌自带过期时间
- bcrypt：自适应哈希，rounds=12 表示 2^12=4096 次迭代，抗暴力破解
- itsdangerous：Flask 生态的签名库，用于生成有时限的 URL 安全令牌
- FastAPI Depends：依赖注入系统，用于在路由处理前自动执行鉴权逻辑

关联文件：
- app/core/config.py — 提供 secret_key、bcrypt_rounds、令牌过期时间等配置
- app/api/auth.py — 调用 create_access_token / verify_password 等函数
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import bcrypt

# python-jose：JWT 的 Python 实现，支持 HS256 等算法
from jose import jwt, JWTError

# itsdangerous：Flask 作者开发的签名库，用于生成有时限的 URL 安全令牌
# URLSafeTimedSerializer：生成 URL 安全（无特殊字符）且带时间戳的签名
# SignatureExpired：令牌过期异常
# BadSignature：签名无效异常（被篡改或密钥不匹配）
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from app.core.config import settings

# JWT 签名算法，HS256 = HMAC-SHA256，对称加密（签名和验证用同一个密钥）
ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# 密码哈希（bcrypt）
# ---------------------------------------------------------------------------
# bcrypt 是工业级密码哈希算法，特点：
# 1. 自带盐值（salt）：每次哈希自动生成随机盐，相同密码的哈希值不同
# 2. 自适应成本：rounds 越高越慢，可随硬件升级提高，抗暴力破解
# 3. 不可逆：哈希是单向函数，无法从哈希值反推原始密码
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    对密码进行 bcrypt 哈希

    参数：
        password: 明文密码
    返回：
        哈希后的密码字符串（含算法标识、cost、盐值和哈希值）

    流程：str → bytes（encode）→ bcrypt 哈希 → bytes → str（decode）
    bcrypt 操作需要 bytes 类型，所以需要 encode/decode 转换
    """
    return bcrypt.hashpw(
        password.encode("utf-8"),                   # str → bytes：bcrypt 只接受 bytes
        bcrypt.gensalt(rounds=settings.bcrypt_rounds),  # 生成随机盐，rounds=12 = 4096 次迭代
    ).decode("utf-8")                               # bytes → str：存入数据库需要字符串


def verify_password(password: str, hashed: str) -> bool:
    """
    验证明文密码是否匹配哈希值

    参数：
        password: 用户输入的明文密码
        hashed: 数据库中存储的哈希值
    返回：
        True = 匹配，False = 不匹配

    注意：bcrypt.checkpw 的参数顺序是 (明文, 哈希)，不要搞反
    """
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT 令牌（JSON Web Token）
# ---------------------------------------------------------------------------
# JWT 结构：Header.Payload.Signature（三段用 . 连接的 Base64 字符串）
# - Header：算法和类型 {"alg":"HS256","typ":"JWT"}
# - Payload：自定义数据 + 标准字段（exp=过期时间, type=令牌类型, jti=唯一ID）
# - Signature：用 secret_key 对前两段的签名，防止篡改
#
# 本项目使用两种令牌：
# - access_token：短期（15 分钟），用于 API 鉴权
# - refresh_token：长期（7 天），用于无感续期 access_token
# 通过 payload 中的 "type" 字段区分，防止用 refresh_token 调用 API
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    签发访问令牌

    参数：
        data: 要写入令牌的数据（通常包含 user_id, email, tenant_id, role）
        expires_delta: 自定义过期时间，None 则使用配置中的默认值
    返回：
        JWT 字符串

    {**data, "type": "access", "exp": expire} 是字典解包语法：
    - **data 将传入的字典展开为键值对
    - 后面的 "type" 和 "exp" 会覆盖 data 中同名的键（如果有的话）
    """
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload = {**data, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """
    签发刷新令牌

    参数：
        data: 要写入令牌的数据（与 access_token 相同）
    返回：
        JWT 字符串

    与 access_token 的区别：
    - type="refresh"：标识为刷新令牌，不能用于 API 鉴权
    - jti=uuid4()：唯一标识符，用于令牌撤销（黑名单）
    - 有效期更长（7 天 vs 15 分钟）
    """
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload = {**data, "type": "refresh", "exp": expire, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def verify_access_token(token: str) -> Optional[dict]:
    """
    验证访问令牌

    参数：token — JWT 字符串
    返回：验证成功返回 payload 字典，失败返回 None
    """
    return _verify_token(token, expected_type="access")


def verify_refresh_token(token: str) -> Optional[dict]:
    """
    验证刷新令牌

    参数：token — JWT 字符串
    返回：验证成功返回 payload 字典，失败返回 None
    """
    return _verify_token(token, expected_type="refresh")


def _verify_token(token: str, expected_type: str) -> Optional[dict]:
    """
    JWT 验证的内部实现

    参数：
        token: JWT 字符串
        expected_type: 期望的令牌类型（"access" 或 "refresh"）
    返回：
        验证成功返回 payload 字典，失败返回 None

    验证流程：
    1. jwt.decode 用 secret_key 解码并验证签名，签名不匹配会抛 JWTError
    2. 检查 payload 中的 type 是否与期望类型匹配（防止用 refresh_token 调 API）
    3. exp（过期时间）由 jwt.decode 自动检查，过期会抛 ExpiredSignatureError（JWTError 的子类）
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except JWTError:
        # JWTError 包含：签名无效、令牌过期、格式错误等所有异常
        return None


# ---------------------------------------------------------------------------
# 密码重置令牌（itsdangerous，30 分钟有效）
# ---------------------------------------------------------------------------
# 与 JWT 不同，itsdangerous 的令牌更轻量，适合"一次性"场景：
# - 不需要存储在数据库中
# - 自带签名防篡改 + 时间戳防过期
# - URL 安全编码（不含 +/= 等特殊字符），适合放在链接中
# ---------------------------------------------------------------------------

def _get_serializer() -> URLSafeTimedSerializer:
    """
    获取签名序列化器（懒加载）

    使用与 JWT 相同的 secret_key，简化密钥管理
    URLSafeTimedSerializer 会生成 URL 安全的 Base64 编码
    """
    return URLSafeTimedSerializer(settings.secret_key)


def generate_password_reset_token(email: str) -> str:
    """
    生成密码重置令牌

    参数：email — 用户邮箱
    返回：签名令牌字符串（URL 安全）

    生成的令牌会嵌入重置密码邮件的链接中，如：
    http://localhost:5173/reset-password?token=xxx
    """
    return _get_serializer().dumps(email)


def verify_password_reset_token(token: str) -> Optional[str]:
    """
    验证密码重置令牌

    参数：token — 待验证的令牌字符串
    返回：验证成功返回邮箱地址，失败返回 None

    max_age=1800：令牌 30 分钟（1800 秒）后过期
    过期抛 SignatureExpired，签名被篡改抛 BadSignature
    """
    try:
        return _get_serializer().loads(token, max_age=1800)
    except (SignatureExpired, BadSignature):
        return None


# ---------------------------------------------------------------------------
# 邮箱验证令牌（itsdangerous，24 小时有效）
# ---------------------------------------------------------------------------
# 与密码重置令牌使用相同的签名机制，只是有效期不同
# ---------------------------------------------------------------------------

def generate_email_verification_token(email: str) -> str:
    """
    生成邮箱验证令牌

    参数：email — 待验证的邮箱地址
    返回：签名令牌字符串（URL 安全）
    """
    return _get_serializer().dumps(email)


def verify_email_verification_token(token: str) -> Optional[str]:
    """
    验证邮箱验证令牌

    参数：token — 待验证的令牌字符串
    返回：验证成功返回邮箱地址，失败返回 None

    max_age=86400：令牌 24 小时（86400 秒）后过期
    邮箱验证给更长时间，因为用户可能不会立即点击邮件中的链接
    """
    try:
        return _get_serializer().loads(token, max_age=86400)
    except (SignatureExpired, BadSignature):
        return None


# ---------------------------------------------------------------------------
# FastAPI 鉴权依赖
# ---------------------------------------------------------------------------
# FastAPI 的 Depends 机制是"依赖注入"：
# - 在路由函数参数中声明 Depends(get_current_user)，FastAPI 会自动调用它
# - 如果鉴权失败（抛 HTTPException），请求不会到达路由函数
# - 如果鉴权成功，返回值会作为参数传入路由函数
#
# 使用示例：
#   @router.get("/me")
#   async def me(user=Depends(get_current_user)):  # 自动鉴权
#       return user
#
#   @router.get("/admin")
#   async def admin(user=Depends(require_role("admin"))):  # 要求 admin 角色
#       return {"message": "admin only"}
# ---------------------------------------------------------------------------

from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession


async def get_current_user(request: Request) -> dict:
    """
    从请求头中提取并验证当前用户

    参数：request — FastAPI 请求对象（由框架自动注入）
    返回：JWT payload 字典（包含 user_id, email, tenant_id, role 等）

    鉴权流程：
    1. 从 Authorization 头提取 Bearer 令牌
    2. 验证令牌有效性和过期时间
    3. 验证失败抛 401 异常，成功返回 payload

    注意：这个函数不查数据库，只验证令牌——这是 JWT 无状态的优势
    缺点是令牌签发后无法主动撤销（除非实现黑名单）
    """
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "未提供认证令牌", "details": None},
        )
    # auth 格式为 "Bearer <token>"，split(" ", 1)[1] 取令牌部分
    token = auth.split(" ", 1)[1]
    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "无效的访问令牌", "details": None},
        )
    return payload


def require_role(*allowed_roles: str):
    """
    角色鉴权依赖工厂

    参数：*allowed_roles — 允许访问的角色列表（可变参数）
    返回：FastAPI 依赖函数

    用法示例：
        require_role("admin")           — 只允许 admin
        require_role("admin", "editor") — 允许 admin 或 editor

    这是一个"工厂函数"——它返回一个函数，而不是直接返回结果。
    因为 FastAPI 的 Depends 需要一个可调用对象，
    而角色列表需要在声明时就确定（不能运行时才传参）。

    *allowed_roles 是 Python 的可变参数语法：
    - require_role("admin") → allowed_roles = ("admin",)
    - require_role("admin", "editor") → allowed_roles = ("admin", "editor")
    """
    async def _check(user=Depends(get_current_user), db: AsyncSession = Depends(lambda: None)) -> dict:
        # 从 JWT payload 中获取角色，默认为 "user"
        role = user.get("role", "user")
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": f"需要 {', '.join(allowed_roles)} 权限", "details": None},
            )
        return user
    return _check
