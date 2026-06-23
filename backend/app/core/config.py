"""
ChatBI v2 — Application Configuration

All magic numbers, timeouts, thresholds, and secrets are centralized here.
Environment variables control everything. No hardcoded values in code.

对标: Claude Code config system + v1 经验教训 #1 (环境配置集中化)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Application ──────────────────────────────────────────
    app_name: str = "ChatBI v2"
    app_version: str = "0.1.0"
    debug: bool = False
    api_prefix: str = "/chat-bi/api/v1"

    # ── Server ───────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8999
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:28080"]

    # ── Database (PostgreSQL) ────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/chatbi"

    # ── Redis ────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl: int = 300
    redis_semantic_cache_threshold: float = 0.95

    # ── Milvus ───────────────────────────────────────────────
    milvus_url: str = "http://localhost:19530"
    milvus_token: str = "root:Milvus"
    milvus_dim: int = 1024

    # ── LLM ──────────────────────────────────────────────────
    llm_url: str = "http://localhost:8000/v1"
    llm_model: str = "qwen3-30b"
    llm_api_key: str = "EMPTY"
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.0
    llm_timeout: int = 60  # seconds

    # ── Embedding ────────────────────────────────────────────
    embedding_url: str = "http://localhost:8000/v1"
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_api_key: str = "EMPTY"

    # ── Security ─────────────────────────────────────────────
    secret_key: str = "CHANGE_ME_SECRET_KEY"
    fernet_key: str = "CHANGE_ME_FERNET_KEY"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    jwt_refresh_expire_days: int = 7
    bcrypt_rounds: int = 12
    max_login_attempts: int = 5
    login_lock_minutes: int = 30

    # ── SQL Execution ────────────────────────────────────────
    sql_execution_timeout: int = 30  # seconds
    sql_max_rows: int = 10000
    sql_result_chunk_size: int = 1000
    sql_self_heal_max_rounds: int = 2
    sql_self_heal_circuit_breaker: int = 3  # consecutive failures across queries

    # ── Agent ─────────────────────────────────────────────────
    agent_max_llm_calls_per_query: int = 20
    agent_loop_recursion_limit: int = 50

    # ── Context Compression ──────────────────────────────────
    compression_token_threshold: float = 0.70
    compression_keep_recent_turns: int = 3
    compression_max_consecutive_failures: int = 3

    # ── Semantic Cache ───────────────────────────────────────
    semantic_cache_similarity_threshold: float = 0.95
    semantic_cache_max_age_hours: int = 24

    # ── RAG ──────────────────────────────────────────────────
    rag_vector_top_k: int = 20
    rag_similarity_threshold: float = 0.5
    rag_max_fewshot_examples: int = 3

    # ── Rate Limiting ────────────────────────────────────────
    rate_limit_queries_per_minute: int = 30
    rate_limit_login_per_minute: int = 5

    # ── Memory ───────────────────────────────────────────────
    memory_max_recall_count: int = 5
    memory_dir: str = "memory"  # relative to backend/

    # ── Logging ──────────────────────────────────────────────
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_retention_days: int = 14

    # ── Audit ────────────────────────────────────────────────
    audit_enabled: bool = True

    # ── Prompt Dump (debug/observability) ────────────────────
    prompt_dump_enabled: bool = False
    prompt_dump_dir: str = "logs/prompts"

    model_config = {
        "env_file": os.getenv("ENV_FILE", str(Path(__file__).resolve().parent.parent.parent / ".env")),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "env_nested_delimiter": "__",
    }


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — load once, reuse everywhere."""
    return Settings()


def validate_settings_on_startup() -> None:
    """
    Check for placeholder secrets on startup — refuse to start if found.

    对标 Claude Code Trust 建立时序: 启动时检测 CHANGE_ME 占位符 → 拒绝启动
    v1 经验教训 #44: Fernet 密钥硬编码在 .env.example
    """
    settings = get_settings()
    placeholder_prefix = "CHANGE_ME"

    critical_fields = [
        ("SECRET_KEY", settings.secret_key),
        ("FERNET_KEY", settings.fernet_key),
    ]

    errors = []
    for name, value in critical_fields:
        if value.startswith(placeholder_prefix):
            errors.append(
                f"❌ {name} is set to placeholder '{value}'. "
                f"Please generate a real key and update your .env file."
            )

    if errors:
        msg = (
            "\n" + "=" * 60 + "\n"
            "🔐 SECURITY: Placeholder secrets detected!\n"
            + "=" * 60 + "\n"
            + "\n".join(errors) + "\n"
            + "=" * 60 + "\n"
            "Generate keys:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            + "=" * 60 + "\n"
        )
        raise RuntimeError(msg)
