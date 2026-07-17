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
from pydantic import field_validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    数值范围校验 (对标 O16): 关键参数用 @field_validator 确保运行时值在合理范围。
    校验失败 → pydantic ValidationError → 应用拒绝启动 (fail-closed)。
    """

    # ── Application ──────────────────────────────────────────
    app_name: str = "ChatBI v2"
    app_version: str = "0.1.0"
    debug: bool = False
    api_prefix: str = "/chat-bi/api/v1"

    # ── Server ───────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8999
    # CORS 来源走 .env (不同部署环境不同, 不写死 localhost)
    cors_origins: list[str] = ["CHANGE_ME_CORS_ORIGINS"]

    # ── Database (PostgreSQL) ────────────────────────────────
    # 占位符: 真实值走 .env, 启动检测拒绝占位符启动 (fail-closed)
    database_url: str = "CHANGE_ME_DATABASE_URL"
    # 元数据 DB 连接池参数 (对标 O12: 不硬编码, 走环境变量)
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── Redis ────────────────────────────────────────────────
    # 占位符: url/凭证走 .env (生产有密码, 不能写死 localhost 无密码)
    redis_url: str = "CHANGE_ME_REDIS_URL"

    # ── Milvus ───────────────────────────────────────────────
    # 占位符: url/token 走 .env (root:Milvus 是默认凭证, 不能写死)
    milvus_url: str = "CHANGE_ME_MILVUS_URL"
    milvus_token: str = "CHANGE_ME_MILVUS_TOKEN"
    # 向量存储后端: milvus(生产) / mock(测试/降级, 纯内存)
    vector_store_backend: str = "milvus"
    # collection 名前缀: 测试/开发/多实例隔离 (对标 PG 库隔离)。
    # 加在所有 collection 名前 → 测试用 "test_" 前缀, 和开发数据物理隔离, 互不可见。
    # 空 = 无前缀 (生产默认)。测试在 conftest 设 VECTOR_STORE_COLLECTION_PREFIX=test_。
    vector_store_collection_prefix: str = ""

    # ── LLM ──────────────────────────────────────────────────
    # 占位符: v2 用 OpenAI 兼容协议 (讯飞 MAAS 等), url/model/key 走 .env
    llm_url: str = "CHANGE_ME_LLM_URL"
    llm_model: str = "CHANGE_ME_LLM_MODEL"
    llm_api_key: str = "CHANGE_ME_LLM_API_KEY"
    # 推理模型(如 Qwen3.6)思考阶段消耗大量 token (实测 SQL/图表 700~1500 reasoning_tokens),
    # 输出预算必须覆盖"思考 + 正式输出", 否则 finish_reason=length 截断 → content 为空。
    # 256K 是 Qwen3.6 上下文上限内的安全输出预算, 换模型走环境变量调整。
    llm_max_tokens: int = 262144  # 256 * 1024
    llm_temperature: float = 0.0
    llm_timeout: int = 600  # seconds (批量推断 100+ 表需较长时间)

    # ── Embedding ────────────────────────────────────────────
    # 决策: 本地 BGE-large-zh (离线、确定、不依赖讯飞非标准协议)
    # embedding_backend: local=本地 sentence-transformers / api=外部 OpenAI 兼容
    embedding_backend: str = "local"
    # 本地模型路径 (项目内, 不入 git; 下载脚本 backend/scripts/download_embedding_model.py)
    # 用绝对路径默认值, 开发期指 backend/models/bge-large-zh-v1.5/
    embedding_model_path: str = str(
        Path(__file__).resolve().parent.parent.parent / "models" / "bge-large-zh-v1.5"
    )
    embedding_dim: int = 1024  # BGE-large-zh-v1.5 维度, 对标 spec RAG-001
    # api 模式备用 (embedding_backend="api" 时启用)
    embedding_url: str = "CHANGE_ME_EMBEDDING_URL"
    embedding_model: str = "CHANGE_ME_EMBEDDING_MODEL"
    embedding_api_key: str = "CHANGE_ME_EMBEDDING_API_KEY"

    # ── Security ─────────────────────────────────────────────
    secret_key: str = "CHANGE_ME_SECRET_KEY"
    fernet_key: str = "CHANGE_ME_FERNET_KEY"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8h (开发友好, 生产可通过环境变量调短)
    jwt_refresh_expire_days: int = 30
    bcrypt_rounds: int = 12
    max_login_attempts: int = 5
    login_lock_minutes: int = 30

    # ── SQL Execution ────────────────────────────────────────
    sql_execution_timeout: int = 30  # seconds
    sql_max_rows: int = 10000
    sql_slow_query_threshold: float = 10.0  # 秒; 超过标记为慢查询 (DSO-07, 可配置)
    sql_result_chunk_size: int = 1000
    sql_self_heal_max_rounds: int = 2
    sql_self_heal_circuit_breaker: int = 3  # consecutive failures across queries

    # ── Business DB Engine Pool (对标 O11/O12/O14) ────────
    # 动态业务库连接池参数 (datasource_engine.py 用)
    # pool_size: 每个引擎的常驻连接数 (大部分数据源同时只有 1-2 个查询)
    business_db_pool_size: int = 2
    # max_overflow: 超出 pool_size 后允许的临时连接 (突发并发用, 空闲后回收)
    business_db_max_overflow: int = 3
    # pool_recycle: 连接存活时间 (秒), 到期自动换新 (防 DB 侧断连/超时)
    business_db_pool_recycle: int = 1800

    # ── Agent ─────────────────────────────────────────────────
    agent_max_llm_calls_per_query: int = 20
    agent_loop_recursion_limit: int = 50

    # ── LLM Client (HTTP 连接/重试, 生产运维常调) ────────────
    llm_max_retries: int = 3  # 429/连接错误重试次数
    llm_retry_base_delay: float = 1.0  # 重试指数退避基数 (秒)
    llm_connect_timeout: float = 10.0  # HTTP 连接超时 (秒)
    llm_write_timeout: float = 10.0  # HTTP 写入超时 (秒)
    llm_pool_timeout: float = 10.0  # HTTP 连接池等待超时 (秒)

    # ── Conversation (对话相关, 生产运维常调) ────────────────
    conversation_title_max_length: int = 16  # 对话标题最大长度
    state_store_row_sample_limit: int = 50  # StateStore 结果采样行数 (防大结果撑爆 JSONL)

    # ── Scan (数据源扫描, 生产运维常调) ──────────────────────
    scan_llm_enrichment_timeout: int = 600  # 扫描 LLM 推断/知识图谱超时 (秒)
    # ChatBI 系统表 (元数据表), 扫描/问题生成时跳过 (用户不会查, 跳过省 60%+ LLM 调用)
    # 新增系统表时只需改此处, 无需改多处硬编码
    system_tables: list[str] = [
        "tenants", "users", "data_sources", "semantic_models",
        "conversations", "saved_queries", "audit_logs",
        "dashboards", "dashboard_widgets",
    ]

    # ── File Paths (持久化目录, 生产运维常调) ──────────────────
    state_store_dir: str = "data/states"  # 对话状态 JSONL 目录
    checkpointer_dir: str = "data/checkpoints"  # Checkpointer JSONL 目录

    # ── Skills (对标 S1: 多租户隔离, skills/{tenant_id}/) ────
    skills_dir: str = "skills"  # relative to backend/

    # ── Context Compression ──────────────────────────────────
    compression_token_threshold: float = 0.70
    compression_keep_recent_turns: int = 3
    compression_max_consecutive_failures: int = 3

    # ── RAG ──────────────────────────────────────────────────
    rag_vector_top_k: int = 20
    rag_similarity_threshold: float = 0.35  # BGE 中文分数分布偏低, 0.5 漏召回; 可调
    rag_max_fewshot_examples: int = 3
    rag_max_schema_tables: int = 10  # 关系扩展后总表数上限 (种子表+桥接表+1跳邻居, 10张足够覆盖绝大多数查询)

    # ── Graph (知识图谱中间件, NetworkX) ─────────────────────
    graph_max_join_path_hops: int = 4  # JOIN 路径最大跳数 (Dijkstra 搜索上限)
    graph_community_algorithm: str = "label_propagation"  # 社区发现算法 (label_propagation | greedy_modularity)
    graph_expand_use_community: bool = True  # 智能扩展是否包含社区补全
    graph_join_path_in_prompt: bool = True  # 是否在 SQL prompt 注入【JOIN 路径】块

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

    # ── Scheduler (定时任务: 健康检查/元数据刷新/任务清理) ────
    scheduler_enabled: bool = True  # 总开关, False 时不启动任何定时任务
    datasource_health_check_interval_seconds: int = 300  # 数据源健康检查间隔 (5 分钟)
    datasource_health_check_max_failures: int = 3  # 连续失败 N 次标记 error
    metadata_auto_refresh_interval_hours: int = 6  # 元数据自动刷新间隔 (6 小时)
    async_task_retention_hours: int = 24  # 已完成异步任务保留时长 (定时清理)

    # ── Prompt Dump (debug/observability) ────────────────────
    prompt_dump_enabled: bool = False
    prompt_dump_dir: str = "logs/prompts"

    # ── Startup Probe (fail-fast) ─────────────────────────────
    # 对标 SEC-004 + SEC-005 + v1 根本模式 (安全 Fail-Closed):
    #   - 必需服务 (startup_required_services) 探测失败 → 拒绝启动 (RuntimeError)
    #   - 可选服务 (redis/milvus) 探测失败 → WARNING 降级, 不阻塞启动 (对标 SEC-005)
    # PG 是元数据真相源 (auth/datasource/semantic/audit), 挂了系统无意义 → 默认必需。
    # 可通过环境变量增减 (如本地开发只跑 PG: STARTUP_REQUIRED_SERVICES=postgres)。
    startup_required_services: list[str] = ["postgres"]
    # 启动探测连接超时 (秒); 每个 service 独立探测, 互不影响
    startup_probe_timeout: int = 5

    # ── Field Validators (对标 O16: 数值范围校验, 防止误配导致运行时异常) ──

    @field_validator("llm_timeout")
    @classmethod
    def _validate_llm_timeout(cls, v: int) -> int:
        if v < 10:
            raise ValueError("llm_timeout 必须 >= 10 秒 (LLM 推理本身需要时间)")
        if v > 3600:
            raise ValueError("llm_timeout 不能超过 3600 秒 (1 小时)")
        return v

    @field_validator("jwt_expire_minutes")
    @classmethod
    def _validate_jwt_expire(cls, v: int) -> int:
        if v < 1:
            raise ValueError("jwt_expire_minutes 必须 >= 1 分钟")
        if v > 43200:  # 30 天
            raise ValueError("jwt_expire_minutes 不能超过 43200 分钟 (30 天)")
        return v

    @field_validator("bcrypt_rounds")
    @classmethod
    def _validate_bcrypt_rounds(cls, v: int) -> int:
        if v < 4:
            raise ValueError("bcrypt_rounds 必须 >= 4 (太低不安全)")
        if v > 20:
            raise ValueError("bcrypt_rounds 不能超过 20 (太高, 登录太慢)")
        return v

    @field_validator("sql_execution_timeout")
    @classmethod
    def _validate_sql_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("sql_execution_timeout 必须 >= 1 秒")
        if v > 300:
            raise ValueError("sql_execution_timeout 不能超过 300 秒 (5 分钟)")
        return v

    @field_validator("db_pool_size", "business_db_pool_size")
    @classmethod
    def _validate_pool_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError("pool_size 必须 >= 1")
        if v > 100:
            raise ValueError("pool_size 不能超过 100 (连接数限制)")
        return v

    @field_validator("db_max_overflow", "business_db_max_overflow")
    @classmethod
    def _validate_max_overflow(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_overflow 不能为负")
        if v > 100:
            raise ValueError("max_overflow 不能超过 100")
        return v

    @field_validator("llm_temperature")
    @classmethod
    def _validate_temperature(cls, v: float) -> float:
        if v < 0.0 or v > 2.0:
            raise ValueError("llm_temperature 必须在 [0.0, 2.0] 范围内")
        return v

    @field_validator("rag_similarity_threshold")
    @classmethod
    def _validate_similarity_threshold(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError("rag_similarity_threshold 必须在 [0.0, 1.0] 范围内")
        return v

    @field_validator("sql_max_rows")
    @classmethod
    def _validate_sql_max_rows(cls, v: int) -> int:
        if v < 1:
            raise ValueError("sql_max_rows 必须 >= 1")
        if v > 1000000:
            raise ValueError("sql_max_rows 不能超过 1000000 (内存保护)")
        return v

    @field_validator("rag_vector_top_k", "rag_max_fewshot_examples", "agent_max_llm_calls_per_query",
                      "embedding_dim", "llm_max_tokens", "rate_limit_queries_per_minute",
                      "rate_limit_login_per_minute", "max_login_attempts", "login_lock_minutes",
                      "startup_probe_timeout", "business_db_pool_recycle",
                      "sql_result_chunk_size", "datasource_health_check_interval_seconds",
                      "conversation_title_max_length", "state_store_row_sample_limit",
                      "compression_keep_recent_turns", "compression_max_consecutive_failures",
                      "memory_max_recall_count", "log_retention_days",
                      "datasource_health_check_max_failures", "metadata_auto_refresh_interval_hours",
                      "async_task_retention_hours", "scan_llm_enrichment_timeout",
                      "agent_loop_recursion_limit", "llm_max_retries",
                      "graph_max_join_path_hops")
    @classmethod
    def _validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("该参数必须 >= 1")
        return v

    @field_validator("llm_retry_base_delay", "llm_connect_timeout", "llm_write_timeout",
                      "llm_pool_timeout", "sql_slow_query_threshold")
    @classmethod
    def _validate_positive_float(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("该参数必须 > 0")
        return v

    @field_validator("jwt_refresh_expire_days")
    @classmethod
    def _validate_refresh_expire(cls, v: int) -> int:
        if v < 1:
            raise ValueError("jwt_refresh_expire_days 必须 >= 1 天")
        if v > 90:
            raise ValueError("jwt_refresh_expire_days 不能超过 90 天 (安全)")
        return v

    @field_validator("compression_token_threshold")
    @classmethod
    def _validate_compression_threshold(cls, v: float) -> float:
        if v <= 0.0 or v > 1.0:
            raise ValueError("compression_token_threshold 必须在 (0.0, 1.0] 范围内")
        return v

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
    Check for placeholder secrets on startup — refuse to start if critical ones found.

    对标 Claude Code Trust 建立时序: 启动时检测 CHANGE_ME 占位符 → 拒绝启动
    v1 经验教训 #44: Fernet 密钥硬编码在 .env.example

    分两级:
      - critical: 缺失 → RuntimeError 拒绝启动 (安全 Fail-Closed)
      - warning:  缺失 → WARNING 日志, 允许降级启动 (对标 SEC-005)
    """
    import logging

    settings = get_settings()
    placeholder_prefix = "CHANGE_ME"

    # ── 必需字段: 缺失拒绝启动 ────────────────────────────────
    critical_fields = [
        ("DATABASE_URL", settings.database_url),
        ("SECRET_KEY", settings.secret_key),
        ("FERNET_KEY", settings.fernet_key),
        ("LLM_URL", settings.llm_url),
        ("LLM_MODEL", settings.llm_model),
        ("LLM_API_KEY", settings.llm_api_key),
    ]

    # ── 可选字段: 缺失降级警告 ────────────────────────────────
    warning_fields: list[tuple[str, str]] = [
        ("REDIS_URL", settings.redis_url),
        ("MILVUS_URL", settings.milvus_url),
        ("MILVUS_TOKEN", settings.milvus_token),
    ]
    # CORS_ORIGINS 是 list[str], 默认 ["CHANGE_ME_CORS_ORIGINS"], 需检查每个元素
    for item in settings.cors_origins:
        if item.startswith(placeholder_prefix):
            warning_fields.append(("CORS_ORIGINS", item))
            break  # 一个占位符就够了
    # embedding API 模式才需要这 3 个, local 模式可忽略
    if settings.embedding_backend == "api":
        warning_fields += [
            ("EMBEDDING_URL", settings.embedding_url),
            ("EMBEDDING_MODEL", settings.embedding_model),
            ("EMBEDDING_API_KEY", settings.embedding_api_key),
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

    # ── 可选字段: 只打 WARNING ────────────────────────────────
    logger = logging.getLogger("app.config")
    for name, value in warning_fields:
        if value.startswith(placeholder_prefix):
            logger.warning(
                "⚠️  %s is set to placeholder '%s' — feature will be degraded. "
                "Update .env for full functionality.",
                name, value,
            )
