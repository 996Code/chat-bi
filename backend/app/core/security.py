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
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=get_settings().bcrypt_rounds),
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
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
    """模块级缓存的 Fernet 实例（密钥来自 settings.fernet_key）。"""
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(get_settings().fernet_key.encode())
    return _fernet_instance


def encrypt_password(plain: str) -> str:
    """加密数据源密码 → 返回 Fernet token 字符串。"""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_password(token: str) -> str:
    """解密数据源密码 → 返回明文。密钥错误抛 InvalidToken。"""
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


# JWT
def create_access_token(data: dict[str, Any]) -> str:
    """Create a JWT access token with all required auth fields.

    对标 v1 经验教训 #3: token 必须包含所有鉴权字段
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
    """
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


# 注: SQL 安全校验已迁移到 app.core.sql_validator (T030, sqlglot AST 三层校验)
# 旧的 validate_sql_select_only (字符串匹配占位) 已删除 (v1 教训 #46: AST 非字符串前缀)
