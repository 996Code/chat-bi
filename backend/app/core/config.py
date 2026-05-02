from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    # App
    app_env: str = "development"

    # Secrets (required — must be set via environment variables)
    secret_key: str
    data_source_encryption_key: str

    # Database (SQLite for v1 dev, PostgreSQL for prod)
    database_url: str = "sqlite+aiosqlite:///./chatbi.db"

    # JWT
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # LLM (OpenAI-compatible API)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"

    # SMTP (email verification / password reset)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # Bcrypt
    bcrypt_rounds: int = 12

    @field_validator("secret_key", "data_source_encryption_key")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("This field must be set via environment variable")
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
