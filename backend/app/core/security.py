"""
ChatBI v2 — Security Utilities

对标: Claude Code 安全默认 (Fail-Closed) + v1 security.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
import bcrypt
from cryptography.fernet import Fernet

from app.core.config import get_settings

# Password hashing (use bcrypt directly, passlib has compat issues with newer bcrypt)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    为什么用 bcrypt 而非其他:
      - bcrypt 内置 salt + 可调 cost factor (rounds), 适配硬件性能演进
      - 相比 PBKDF2/Argon2, bcrypt 有 GPU 抗性 (内存需求高于计算)
      - rounds 来自 config.bcrypt_rounds, 可在不影响用户登录体验的前提下调高
    """
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=get_settings().bcrypt_rounds),
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    安全性: bcrypt.checkpw 自带 timing-safe 比较, 不因密码长度/内容差异产生可观测时间差。
    调用方注意事项: 永远不要返回"密码错误"还是"用户不存在"之外的区别信息, 防枚举。
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# DataSource password encryption (Fernet symmetric, 对标 v1 #38/#44)
#
# 区别于上面的 bcrypt (用户密码: 单向哈希, 不可还原),
# 数据源密码需要解密后才能连库 → 用 Fernet 对称加密, 密钥从 config 注入。
# 密钥与密文理论上同机仍有风险 (#38), 生产建议 KMS/IAM; 当前至少不硬编码 (#44)。

_fernet_instance: Fernet | None = None


def _get_fernet() -> Fernet:
    """模块级缓存的 Fernet 实例（密钥来自 settings.fernet_key）。

    为什么模块级缓存:
      - Fernet 实例化开销小, 但每次调用都重新创建仍是不必要的重复
      - 模块级单例保证整个进程生命周期内复用同一个实例, 减少 GC 压力
    线程安全: Fernet 实例本身是线程安全的, 无需加锁。
    """
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(get_settings().fernet_key.encode())
    return _fernet_instance


def encrypt_password(plain: str) -> str:
    """加密数据源密码 → 返回 Fernet token 字符串。

    加密后的 token 可安全存储在数据库中, 即使数据库泄露也无法还原明文。
    生产环境建议: 将 fernet_key 存储在 KMS/密钥管理服务中, 而非配置文件。
    """
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_password(token: str) -> str:
    """解密数据源密码 → 返回明文。密钥错误抛 InvalidToken。

    注意: decrypt 失败时抛 cryptography.fernet.InvalidToken,
    调用方应捕获并返回"数据源密码解密失败, 请检查密钥配置"。
    不要将原始异常暴露给用户, 以免泄露密钥信息。
    """
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


# JWT
def create_access_token(data: dict[str, Any]) -> str:
    """Create a JWT access token with all required auth fields.

    对标 v1 经验教训 #3: token 必须包含所有鉴权字段

    data 要求: 至少包含 user_id, email, tenant_id, role 四个字段。
    exp 由函数自动添加, 调用方无需传入。
    type 标记为 "access" 用于区分 refresh token, 避免 access token 被用于 refresh 端点。
    """
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict[str, Any]) -> str:
    """
    Create a JWT refresh token — must contain ALL same auth fields as access token.

    对标 v1 经验教训 #20: refresh token payload 需包含所有鉴权字段

    设计决策: refresh token 的过期时间远长于 access token (天级 vs 分钟级),
    因此 payload 必须包含完整的鉴权字段, 避免 refresh 时需要回查数据库。
    如果一个 refresh token 泄露, 攻击者只能获得同权限的 token, 无法提权。
    """
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure.

    验证内容包括: 签名完整性、过期时间、颁发者(如配置)。

    异常处理:
      - jwt.ExpiredSignatureError: token 已过期, 调用方应返回 401
      - jwt.JWTError: 签名无效/token 被篡改, 调用方应返回 401
    注意: decode 不会验证 payload 中的 type 字段, 调用方需自行检查。
    """
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


# 注: SQL 安全校验已迁移到 app.core.sql_validator (T030, sqlglot AST 三层校验)
# 旧的 validate_sql_select_only (字符串匹配占位) 已删除 (v1 教训 #46: AST 非字符串前缀)
