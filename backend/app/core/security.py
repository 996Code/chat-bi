from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import bcrypt

from jose import jwt, JWTError
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from app.core.config import settings

ALGORITHM = "HS256"


# --- Password hashing (bcrypt, cost 12) ---

def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.bcrypt_rounds),
    ).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# --- JWT tokens ---

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload = {**data, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload = {**data, "type": "refresh", "exp": expire, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def verify_access_token(token: str) -> Optional[dict]:
    return _verify_token(token, expected_type="access")


def verify_refresh_token(token: str) -> Optional[dict]:
    return _verify_token(token, expected_type="refresh")


def _verify_token(token: str, expected_type: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except JWTError:
        return None


# --- Password reset tokens (itsdangerous, 30min) ---

def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key)


def generate_password_reset_token(email: str) -> str:
    return _get_serializer().dumps(email)


def verify_password_reset_token(token: str) -> Optional[str]:
    try:
        return _get_serializer().loads(token, max_age=1800)
    except (SignatureExpired, BadSignature):
        return None


# --- Email verification tokens (itsdangerous, 24h) ---

def generate_email_verification_token(email: str) -> str:
    return _get_serializer().dumps(email)


def verify_email_verification_token(token: str) -> Optional[str]:
    try:
        return _get_serializer().loads(token, max_age=86400)
    except (SignatureExpired, BadSignature):
        return None
