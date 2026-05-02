from pydantic import BaseModel, EmailStr, field_validator
import re


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码至少8位")
        if not re.search(r"[a-zA-Z]", v):
            raise ValueError("密码必须包含字母")
        if not re.search(r"[0-9]", v):
            raise ValueError("密码必须包含数字")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码至少8位")
        if not re.search(r"[a-zA-Z]", v):
            raise ValueError("密码必须包含字母")
        if not re.search(r"[0-9]", v):
            raise ValueError("密码必须包含数字")
        return v


class EmailVerifyRequest(BaseModel):
    token: str
