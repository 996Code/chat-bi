"""
ChatBI 全局配置 — 基于 Pydantic BaseSettings 的环境变量绑定

本文件是整个项目的"配置中心"，所有魔法数字和可调参数都集中在这里。
其他模块通过 `from app.core.config import settings` 获取配置单例。

核心机制：
1. BaseSettings：Pydantic 提供的配置基类，自动从环境变量读取值
   - 字段名大写化后对应环境变量名（如 secret_key → SECRET_KEY）
   - 支持从 .env 文件自动加载，无需手动 export
2. field_validator：对关键字段做启动时校验，配置错误立即报错而非运行时才发现
3. extra="forbid"：禁止传入未定义的环境变量，防止拼写错误导致的隐蔽 bug

配置分组：
- App / Secrets  — 应用基础和密钥
- Database       — 数据库连接
- JWT            — 令牌过期时间
- LLM            — 大模型参数
- Query          — 查询限制
- Connection Pool — 连接池
- Login Lock     — 登录安全
- Rate Limiting  — 限流
- RAG            — 检索增强生成
- SMTP / CORS    — 邮件和跨域
- Redis / Cache  — 缓存
- Test DB        — 测试数据库
- Docker         — 部署专用

关联文件：
- app/main.py — 读取 settings.cors_origins、settings.api_prefix 等
- app/core/security.py — 读取 settings.secret_key、settings.bcrypt_rounds 等
- app/core/redis_client.py — 读取 settings.redis_url
- .env — 环境变量文件，被 model_config.env_file 指定
"""

from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator
from typing import Optional
from pathlib import Path

# BASE_DIR 指向项目根目录（chat-bi/）
# Path(__file__) 是当前文件路径，.parent 依次向上：config.py → core → app → backend → chat-bi
# 用于定位 .env 文件等根目录下的资源
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # project root


class Settings(BaseSettings):
    """
    全局配置类 — 所有配置项的唯一定义源

    工作原理：
    - 每个类属性就是一个配置项，类型注解决定了值的类型
    - 等号右边是默认值，如果环境变量没设置就使用默认值
    - 没有默认值的字段（如 secret_key）是必填项，缺少时启动报错
    - 环境变量名 = 字段名大写，如 app_env → APP_ENV
    """

    # ---- 应用基础 ----
    app_env: str = "development"   # 运行环境：development / production / docker
    app_port: int = 8000           # 应用端口号（Docker 内部端口）

    # ---- 密钥（必填 — 必须通过环境变量设置，无默认值）----
    # secret_key：JWT 签名密钥，泄露 = 任何人都能伪造令牌
    # data_source_encryption_key：数据源密码的 Fernet 加密密钥
    secret_key: str
    data_source_encryption_key: str

    # ---- 数据库 ----
    # mysql+aiomysql 是 SQLAlchemy 的异步 MySQL 驱动
    # 生产环境通过 DATABASE_URL 环境变量覆盖
    database_url: str = "mysql+aiomysql://root:root@127.0.0.1:3306/chatbi"

    # ---- JWT 令牌 ----
    jwt_algorithm: str = "HS256"                       # JWT 签名算法，HS256 = 对称加密
    access_token_expire_minutes: int = 15              # 访问令牌有效期（分钟），短一点更安全
    refresh_token_expire_days: int = 7                 # 刷新令牌有效期（天），用于无感续期

    # ---- 令牌过期时间（秒）----
    reset_token_expire_seconds: int = 1800             # 密码重置令牌：30 分钟
    verification_token_expire_seconds: int = 86400     # 邮箱验证令牌：24 小时

    # ---- LLM 大模型配置 ----
    # 使用 OpenAI 兼容 API，可对接任意大模型（OpenAI / Azure / 本地部署）
    llm_base_url: str = "https://api.openai.com/v1"    # API 地址
    llm_api_key: str = ""                              # API 密钥
    llm_model: str = "gpt-4o"                          # 默认模型
    # 意图识别：temperature=0.0 确保确定性输出，max_tokens=10 只需一个词
    llm_intent_temperature: float = 0.0
    llm_intent_max_tokens: int = 10
    # SQL 生成：temperature=0.0 保证 SQL 稳定性，max_tokens=2000 允许复杂 SQL
    llm_generation_temperature: float = 0.0
    llm_generation_max_tokens: int = 2000
    # SQL 自愈：执行失败后让 LLM 修复 SQL，最多重试 2 轮
    llm_self_heal_max_retries: int = 2

    # ---- 模型路由（可选 — 默认关闭）----
    # 简单查询用轻量模型，复杂查询用主力模型，节省成本
    # llm_simple_model 为空 = 不启用模型路由，所有查询走 llm_model
    llm_simple_model: str = ""  # Lightweight model for simple queries; empty = disabled
    llm_complex_threshold: int = 6  # Score threshold for "complex" routing

    # ---- 查询限制 ----
    query_max_rows: int = 1000               # 单次查询最大返回行数，防止内存溢出
    sql_execution_timeout: int = 30          # 单条 SQL 执行超时（秒）
    query_pipeline_timeout: int = 60         # 整个查询管道超时（秒），含 LLM 调用
    stream_query_timeout: int = 90           # 流式查询超时（秒），比普通查询更长
    conversation_history_max_turns: int = 5  # 对话上下文保留的轮数

    # ---- 连接池 ----
    # 控制与用户数据源（非 ChatBI 自身数据库）的连接池大小
    pool_min_size: int = 5                   # 最小空闲连接数
    pool_max_size: int = 10                  # 最大连接数
    pool_timeout: int = 30                   # 获取连接超时（秒）
    pool_recycle: int = 3600                 # 连接回收时间（秒）
    # MySQL 默认 wait_timeout=8h，这里 1h 回收避免拿到已被服务端关闭的连接
    # Legacy aliases (kept for backward compatibility)
    # 旧字段名保留兼容，新代码应使用 pool_* 系列命名
    db_pool_size: int = 5
    db_pool_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600

    # ---- 登录安全 ----
    login_max_attempts: int = 5               # 最大登录失败次数
    login_lock_duration_seconds: int = 900    # 锁定时长（秒），5 次失败后锁定 15 分钟

    # ---- 限流配置 ----
    # 格式：rate_limit_<接口>_<指标>，窗口单位为秒
    rate_limit_default_max: int = 60          # 默认：60 次/分钟
    rate_limit_default_window: int = 60
    rate_limit_login_max: int = 10            # 登录：10 次/分钟（防暴力破解）
    rate_limit_login_window: int = 60
    rate_limit_query_max: int = 30            # 查询：30 次/分钟（LLM 调用成本高）
    rate_limit_query_window: int = 60

    # ---- Chroma 向量数据库（RAG 语义检索）----
    # 用于存储表名/字段名的向量嵌入，实现"模糊匹配"表名
    chroma_path: str = "./.chroma"

    # ---- RAG Schema 检索参数 ----
    # 控制"从数据字典中检索多少表/字段"送给 LLM
    rag_max_tables: int = 5                   # 最多返回 5 张表
    rag_max_columns: int = 15                 # 每张表最多 15 个字段
    rag_name_sim_threshold: float = 0.5       # 表名相似度阈值（越低越宽松）
    rag_desc_sim_threshold: float = 0.4       # 描述相似度阈值
    rag_column_sim_threshold: float = 0.5     # 字段名相似度阈值

    # ---- RAG 字段裁剪（两阶段检索）----
    # 第一阶段检索表，第二阶段按相关性裁剪字段，减少 LLM 输入 token
    rag_max_columns_per_query: int = 10       # 每次查询最多送入 10 个字段
    rag_pruning_enabled: bool = True          # 是否启用字段裁剪

    # ---- SMTP 邮件配置（邮箱验证 / 密码重置）----
    smtp_host: str = ""                       # SMTP 服务器地址
    smtp_port: int = 587                      # SMTP 端口，587 = TLS
    smtp_user: str = ""                       # 发件人账号
    smtp_password: str = ""                   # 发件人密码/授权码
    smtp_from: str = ""                       # 发件人显示地址

    # ---- CORS 跨域 ----
    # 允许的前端源地址列表，生产环境应替换为实际域名
    cors_origins: list[str] = ["http://localhost:5173"]

    # ---- API 路径 ----
    api_prefix: str = "/chat-bi/api/v1"      # 所有 API 的统一前缀

    # ---- 前端地址 ----
    # 用于生成邮箱验证、密码重置等链接中的前端页面地址
    frontend_url: str = "http://localhost:5173"

    # ---- 密码哈希 ----
    # bcrypt rounds = 2^12 次迭代，越高越安全但越慢，12 是安全与性能的平衡点
    bcrypt_rounds: int = 12

    # ---- Redis 与查询缓存 ----
    # Redis 用途：查询缓存、限流计数、登录锁定
    redis_url: str = "redis://127.0.0.1:6379/0"  # Redis 连接地址，0 是数据库编号
    query_cache_ttl_seconds: int = 3600      # 默认缓存 TTL：1 小时
    query_cache_ttl_simple: int = 1800       # 简单查询 TTL：30 分钟（单指标，结果变化快）
    query_cache_ttl_complex: int = 7200      # 复杂查询 TTL：2 小时（多步骤，计算成本高）

    # ---- 慢查询告警 ----
    slow_query_threshold_ms: int = 1000  # 慢查询阈值（毫秒），1s（开发环境方便测试）
    slow_query_alert_enabled: bool = False  # 是否启用慢查询告警

    # ---- 元数据自动刷新 ----
    # 定时刷新数据源的表/字段元数据，保持数据字典最新
    metadata_auto_refresh_enabled: bool = False   # 是否启用
    metadata_auto_refresh_interval_minutes: int = 60  # 刷新间隔（分钟）
    metadata_auto_refresh_datasources: list[str] = []  # 指定刷新的数据源 ID，空 = 全部

    # ---- 日志 ----
    log_level: str = "INFO"                   # 日志级别：DEBUG / INFO / WARNING / ERROR

    # ---- Docker / 前端专用 ----
    # 这些字段不在后端代码中使用，但 .env 文件中存在对应变量
    # 因为 extra="forbid"，如果不在这里声明，Pydantic 会报错拒绝启动
    # 所以把它们列出来只是为了让 Pydantic "认识"这些环境变量
    chatbi_port: int = 28080                  # Docker 对外暴露端口
    deploy_mysql_port: int = 3306             # Docker MySQL 端口
    deploy_mysql_root_password: str = ""      # Docker MySQL root 密码
    deploy_mysql_database: str = "chatbi"     # Docker MySQL 数据库名
    deploy_redis_port: int = 6379             # Docker Redis 端口
    vite_base_path: str = "/chat-bi/"         # Vite 前端基础路径
    vite_api_prefix: str = "/chat-bi/api/v1"  # Vite 前端 API 前缀
    api_prefix: str = "/chat-bi/api/v1"       # API 路由前缀（与 vite_api_prefix 保持一致）

    # ---- 测试数据库 ----
    # 用于 init_test_dbs.py / seed_test_db.py 创建测试数据
    # 空值 = 跳过该数据库的测试初始化
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

    # ---- Docker 部署专用 ----
    # Docker 内部服务间通信使用容器名而非 localhost
    docker_redis_host: str = "redis"         # Docker 网络中的 Redis 容器名
    docker_mysql_host: str = "mysql"         # Docker 网络中的 MySQL 容器名

    # ---- 字段校验器 ----
    # @field_validator 是 Pydantic V2 的装饰器，在字段赋值时自动触发
    # 这里校验 secret_key 和 data_source_encryption_key 不能为空
    # 如果 .env 中没设置这两个值，应用启动时就会报错，而不是运行时才发现
    @field_validator("secret_key", "data_source_encryption_key")
    @classmethod
    def not_empty(cls, v: str) -> str:
        """校验必填字段：不能为空或纯空白字符"""
        if not v or not v.strip():
            raise ValueError("This field must be set via environment variable")
        return v

    @field_validator("bcrypt_rounds", mode="after")
    @classmethod
    def _validate_bcrypt_rounds(cls, v: int) -> int:
        """校验 bcrypt 迭代次数：必须在 4-31 之间（2 的幂次范围）"""
        if not (4 <= v <= 31):
            raise ValueError("bcrypt_rounds must be between 4 and 31")
        return v

    @field_validator("access_token_expire_minutes", mode="after")
    @classmethod
    def _validate_access_token_expire(cls, v: int) -> int:
        """校验访问令牌过期时间：至少 1 分钟，最长 24 小时"""
        if not (1 <= v <= 1440):
            raise ValueError("access_token_expire_minutes must be between 1 and 1440")
        return v

    @field_validator("refresh_token_expire_days", mode="after")
    @classmethod
    def _validate_refresh_token_expire(cls, v: int) -> int:
        """校验刷新令牌过期时间：至少 1 天，最长 30 天"""
        if not (1 <= v <= 30):
            raise ValueError("refresh_token_expire_days must be between 1 and 30")
        return v

    # ---- Pydantic 模型配置 ----
    # model_config 是 Pydantic V2 的配置方式（V1 用 Config 内部类）
    model_config = ConfigDict(
        # 从 .env 文件加载环境变量，BASE_DIR / ".env" 指向项目根目录
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        # extra="forbid"：禁止传入未在 Settings 中定义的环境变量
        # 好处：拼写错误（如 DATBASE_URL）会在启动时报错，而不是静默忽略
        extra="forbid",
    )

    # ---- 便捷属性 ----
    # @property 把方法变成属性，访问时不用加括号：settings.is_docker 而非 settings.is_docker()
    @property
    def is_docker(self) -> bool:
        """判断是否运行在 Docker 容器内"""
        return self.app_env == "docker"


# ---- 全局配置单例 ----
# Settings() 在模块导入时实例化一次，之后所有地方 import 的是同一个对象
# 这是 Python 中"模块级单例"的惯用写法——模块只加载一次，对象只创建一次
settings = Settings()
