from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator
from typing import Optional
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # project root


class Settings(BaseSettings):
    # App
    app_env: str = "development"
    app_port: int = 8000

    # Secrets (required — must be set via environment variables)
    secret_key: str
    data_source_encryption_key: str

    # Database (MySQL default — override via DATABASE_URL env var)
    database_url: str = "mysql+aiomysql://root:root@127.0.0.1:3306/chatbi"

    # JWT
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Token expiry (seconds)
    reset_token_expire_seconds: int = 1800
    verification_token_expire_seconds: int = 86400

    # LLM (OpenAI-compatible API)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_intent_temperature: float = 0.0
    llm_intent_max_tokens: int = 10
    llm_generation_temperature: float = 0.0
    llm_generation_max_tokens: int = 2000
    llm_self_heal_max_retries: int = 2

    # Model routing (optional — disabled by default)
    llm_simple_model: str = ""  # Lightweight model for simple queries; empty = disabled
    llm_complex_threshold: int = 6  # Score threshold for "complex" routing

    # Query
    query_max_rows: int = 1000
    sql_execution_timeout: int = 30
    query_pipeline_timeout: int = 60
    stream_query_timeout: int = 90
    conversation_history_max_turns: int = 5

    # Connection pool
    pool_min_size: int = 5
    pool_max_size: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 3600  # MySQL default wait_timeout is 8h; recycle at 1h to avoid stale connections
    # Legacy aliases (kept for backward compatibility)
    db_pool_size: int = 5
    db_pool_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600

    # Login lock
    login_max_attempts: int = 5
    login_lock_duration_seconds: int = 900

    # Rate limiting
    rate_limit_default_max: int = 60
    rate_limit_default_window: int = 60
    rate_limit_login_max: int = 10
    rate_limit_login_window: int = 60
    rate_limit_query_max: int = 30
    rate_limit_query_window: int = 60

    # Chroma vector store (RAG semantic retrieval)
    chroma_path: str = "./.chroma"

    # RAG schema
    rag_max_tables: int = 5
    rag_max_columns: int = 15
    rag_name_sim_threshold: float = 0.5
    rag_desc_sim_threshold: float = 0.4
    rag_column_sim_threshold: float = 0.5

    # RAG column pruning (two-stage retrieval)
    rag_max_columns_per_query: int = 10
    rag_pruning_enabled: bool = True

    # SMTP (email verification / password reset)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # API
    api_prefix: str = "/chat-bi/api/v1"

    # Frontend URL (email verification / password reset links)
    frontend_url: str = "http://localhost:5173"

    # Bcrypt
    bcrypt_rounds: int = 12

    # Redis (query cache, rate limits, login lock)
    redis_url: str = "redis://127.0.0.1:6379/0"  # Redis for query cache, rate limits, login lock
    query_cache_ttl_seconds: int = 3600  # Default cache TTL
    query_cache_ttl_simple: int = 1800   # TTL for simple queries (single metric)
    query_cache_ttl_complex: int = 7200  # TTL for complex queries (multi-step)

    # --- Test database ---
    # MySQL test DB (used by init_test_dbs.py, seed_test_db.py)
    mysql_test_host: str = ""  # empty = skip MySQL test init
    mysql_test_port: int = 3306
    mysql_test_user: str = "root"
    mysql_test_pass: str = ""
    mysql_test_db: str = "chatbi_test"

    # PostgreSQL test DB (used by init_test_dbs.py)
    pg_test_host: str = ""  # empty = skip PG test init
    pg_test_port: int = 5432
    pg_test_user: str = ""
    pg_test_pass: str = ""
    pg_test_db: str = "chatbi_test"

    # Slow query alerting
    slow_query_threshold_ms: int = 1000  # 1s (开发环境方便测试)
    slow_query_alert_enabled: bool = False

    # Metadata auto-refresh
    metadata_auto_refresh_enabled: bool = False
    metadata_auto_refresh_interval_minutes: int = 60
    metadata_auto_refresh_datasources: list[str] = []  # empty = all active datasources

    # Logging
    log_level: str = "INFO"

    # ---- Docker-compose / frontend only (recognized so pydantic doesn't forbid) ----
    chatbi_port: int = 28080
    deploy_mysql_port: int = 3306
    deploy_mysql_root_password: str = ""
    deploy_mysql_database: str = "chatbi"
    deploy_redis_port: int = 6379
    vite_base_path: str = "/chat-bi/"
    vite_api_prefix: str = "/chat-bi/api/v1"
    api_prefix: str = "/chat-bi/api/v1"

    @field_validator("secret_key", "data_source_encryption_key")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("This field must be set via environment variable")
        return v

    model_config = ConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="forbid")


settings = Settings()
