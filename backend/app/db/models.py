"""
数据库模型定义 — 本项目的所有数据表都在这里声明。

核心概念：
  - SQLAlchemy ORM：用 Python 类来描述数据库表，不用手写 SQL 建表。
    每个类 = 一张表，每个类属性 = 一个字段。
  - Mapped[type]：SQLAlchemy 2.0 的类型注解语法，同时声明了 Python 类型和数据库列。
    例如 Mapped[str] 表示这个字段在 Python 中是 str，在数据库中是 VARCHAR。
  - mapped_column()：替代旧版 Column() 的新写法，配合 Mapped 使用，更简洁。
  - GUID 自定义类型：因为 MySQL 没有原生 UUID 类型，我们用 CHAR(36) 存储，
    PostgreSQL 则使用原生 UUID 类型。详见 app/db/types.py。
  - Base：所有模型的基类，继承自 DeclarativeBase（见 app/db/base.py），
    SQLAlchemy 通过它来跟踪所有模型类。

本文件与其它文件的关系：
  - app/db/session.py → 用这些模型执行数据库操作
  - app/db/types.py → 提供 GUID 自定义类型
  - app/db/base.py → 提供 Base 基类
  - app/services/*.py → 业务逻辑层，操作这些模型
  - seed.py → 用这些模型初始化测试数据

模型一览（15 个表）：
  Tenant          — 租户（多租户隔离的顶层实体）
  User            — 用户（属于某个租户，有角色和锁定机制）
  DataSource      — 数据源（用户接入的外部数据库连接信息）
  MetadataConfig  — 元数据配置（数据源的表/字段描述，JSON 格式）
  MetadataConfigVersion — 元数据版本快照（支持配置回滚）
  AuditLog        — 审计日志（记录所有查询操作，合规追溯）
  SavedQuery      — 保存的查询（用户收藏的历史查询）
  Feedback        — 用户反馈（对查询结果的点赞/点踩）
  AnalyticsEvent  — 分析事件（用户行为埋点）
  AsyncQuery      — 异步查询任务（后台执行，轮询获取结果）
  Conversation    — 对话（聊天上下文，包含消息历史）
  Dashboard       — 看板（可视化仪表盘）
  DashboardShare  — 看板分享（生成分享链接，可设密码和过期时间）
  DashboardWidget — 看板组件（看板中的单个图表/表格卡片）
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, CheckConstraint, Text, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.types import GUID


class Tenant(Base):
    """租户表 — 多租户 SaaS 的顶层隔离单位。

    每个租户拥有独立的数据空间，所有业务表都通过 tenant_id 关联到租户。
    这是"共享数据库、共享表结构"的多租户方案（靠 tenant_id 字段隔离数据），
    而不是"每个租户一个数据库"的方案。
    """
    __tablename__ = "tenants"  # 对应数据库中的表名

    # 主键：使用 UUID 而非自增整数，好处是分布式环境下不会冲突
    # GUID 是自定义类型（见 app/db/types.py），MySQL 存为 CHAR(36)，PostgreSQL 用原生 UUID
    # default=uuid.uuid4 — 新建对象时自动生成一个随机 UUID
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 时间字段的设计模式：
    #   default=datetime.now      — Python 侧默认值（创建对象时由 Python 生成时间）
    #   server_default=func.now() — 数据库侧默认值（INSERT 时由数据库 NOW() 生成）
    #   两者同时设置 = 双重保险：即使 Python 没传值，数据库也会自动填充
    #   onupdate=func.now()       — UPDATE 时自动刷新时间（仅数据库侧触发）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class User(Base):
    """用户表 — 系统的登录用户。

    关键设计：
      - 通过 tenant_id 实现多租户隔离（每个用户属于一个租户）
      - 通过 role 字段实现权限控制（admin/user/read_only 三种角色）
      - 内置登录安全机制：失败次数计数 + 账号锁定 + 锁定到期自动解锁
      - 密码存储为 bcrypt 哈希，永远不存明文
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)

    # index=True — 为这个字段创建数据库索引，加速按 tenant_id 查询
    # 多租户系统中几乎所有查询都带 tenant_id 条件，索引至关重要
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)

    # unique=True — 邮箱全局唯一，用于登录标识
    # index=True — 登录时按邮箱查找，索引加速
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # 以下四个字段构成"登录安全"机制：
    # failed_login_attempts — 连续登录失败次数，达到阈值后锁定账号
    # is_locked — 是否已被锁定
    # lock_until — 锁定到期时间，到期后自动解锁（无需管理员手动操作）
    is_active: Mapped[bool] = mapped_column(default=True)
    email_verified: Mapped[bool] = mapped_column(default=False)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    is_locked: Mapped[bool] = mapped_column(default=False)
    lock_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 角色字段 + CheckConstraint 约束
    # CheckConstraint 是数据库级别的约束，确保 role 只能是三个值之一
    # 这比在 Python 代码中校验更安全——即使绕过应用层，数据库也会拒绝非法值
    role: Mapped[str] = mapped_column(String(50), default="user")

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user', 'read_only')", name="ck_user_role"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class DataSource(Base):
    """数据源表 — 用户接入的外部数据库连接配置。

    这是 ChatBI 的核心概念之一：用户把自己的业务数据库（MySQL/PostgreSQL/SQLite）
    接入系统，系统就能读取其表结构，帮助用户用自然语言查询数据。

    安全设计：
      - 用户名和密码使用 Fernet 对称加密存储（见 app/core/encryption.py）
      - 字段名带 _encrypted 后缀，提醒开发者这是密文，不能直接用
      - 使用时通过 decrypt_value() 解密，再构建数据库连接
    """
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # db_type 支持三种数据库：mysql / postgresql / sqlite
    db_type: Mapped[str] = mapped_column(String(50), default="mysql")
    host: Mapped[str] = mapped_column(String(500), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False, default=3306)  # MySQL 默认端口
    database_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 加密存储的数据库凭据 — 解密后才能连接外部数据库
    username_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)

    is_active: Mapped[bool] = mapped_column(default=True)

    # 健康检查相关：定期检测数据源是否可达，不可达时记录错误信息
    last_health_check: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    health_check_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class MetadataConfig(Base):
    """元数据配置表 — 存储数据源的表/字段描述信息。

    元数据是 ChatBI 的"知识库"：告诉 AI 每张表、每个字段是什么意思，
    这样 AI 才能把自然语言翻译成正确的 SQL。

    config 字段存储 JSON 格式的元数据，结构大致如下：
    {
      "tables": [
        {
          "name": "orders",
          "description": "订单表",
          "columns": [
            {"name": "amount", "description": "订单金额（元）", "data_type": "decimal"},
            ...
          ]
        }
      ]
    }
    """
    __tablename__ = "metadata_configs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)

    # datasource_id — 关联到数据源，每个数据源有一份元数据配置
    # 注意：这里没有用 ForeignKey，是逻辑关联而非数据库外键
    # 原因：外键会降低写入性能，且在分布式/微服务场景下跨库外键不可用
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    config: Mapped[str] = mapped_column(Text, nullable=False)  # JSON schema content
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class MetadataConfigVersion(Base):
    """元数据配置版本表 — 保存每次修改的快照，支持版本回滚。

    为什么需要版本表？
      元数据配置是 AI 生成 SQL 的关键输入。如果用户改错了配置，
      可以通过版本快照回滚到之前的状态，而不是重新配置。

    设计思路：
      - config_snapshot：保存该版本的完整配置副本（快照），不是增量
      - version_number：递增的版本号，方便按顺序查找
      - change_summary：人工填写的变更说明，类似 git commit message
    """
    __tablename__ = "metadata_config_versions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)

    # config_id — 关联到 MetadataConfig.id，表示这是哪个配置的版本历史
    config_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON snapshot at this version
    change_summary: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class AuditLog(Base):
    """审计日志表 — 记录所有查询操作的完整轨迹。

    审计日志是企业级 BI 系统的合规要求：
      - 谁在什么时间查了什么数据
      - 查询是否成功，执行了多久
      - 生成了什么 SQL，返回了多少行

    关键设计：
      - user_id 可为空（nullable=True）：某些系统级操作没有具体用户
      - 区分 execution_time_ms（整个 AI 管道耗时）和 sql_execution_time_ms（纯 SQL 执行耗时），
        便于定位性能瓶颈是在 AI 推理还是数据库查询
      - conversation_id 关联到对话，方便按对话查看完整操作链
    """
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # 操作类型，如 "query", "login" 等
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # 操作对象类型
    resource_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # 操作对象 ID
    details: Mapped[str] = mapped_column(Text, default="")  # 操作详情（JSON）
    sql_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI 生成的 SQL
    result_count: Mapped[Optional[int]] = mapped_column(nullable=True)  # 返回行数
    execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)  # Total pipeline time
    sql_execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)  # Pure SQL execution time
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class SavedQuery(Base):
    """保存的查询表 — 用户收藏/保存的历史查询记录。

    用户在聊天界面提问后，可以把满意的查询保存下来，方便下次复用。
    同时记录了查询的执行结果摘要（是否成功、耗时、行数等），
    以及 AI 推荐的图表类型。
    """
    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # 用户给查询起的名字
    query_text: Mapped[str] = mapped_column(Text, nullable=False)  # 用户的自然语言问题
    generated_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI 生成的 SQL
    success: Mapped[Optional[bool]] = mapped_column(nullable=True)  # 查询是否成功
    execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chart_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # AI 推荐的图表类型
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class Feedback(Base):
    """用户反馈表 — 对查询结果的点赞/点踩。

    这是 AI 系统优化的关键数据来源：
      - positive 反馈可以用来微调提示词或训练模型
      - negative 反馈帮助发现 AI 的薄弱环节
      - comment 字段让用户补充说明原因
    """
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    query_id: Mapped[str] = mapped_column(String(100), nullable=False)  # 关联的查询 ID
    rating: Mapped[str] = mapped_column(String(20), nullable=False)  # "positive" 或 "negative"
    comment: Mapped[str] = mapped_column(String(1000), default="")  # 用户补充说明
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class AnalyticsEvent(Base):
    """分析事件表 — 用户行为埋点数据。

    与 AuditLog 的区别：
      - AuditLog 侧重"合规审计"（谁查了什么数据），是安全/合规视角
      - AnalyticsEvent 侧重"产品分析"（用户怎么使用系统），是产品优化视角

    event_name 示例：page_view, query_submit, chart_render, export 等
    """
    __tablename__ = "analytics_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)  # 事件名称
    event_data: Mapped[str] = mapped_column(Text, default="")  # 事件详情（JSON 格式）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class AsyncQuery(Base):
    """异步查询任务表 — 后台执行的查询，通过轮询获取结果。

    为什么需要异步查询？
      AI 生成 SQL + 执行查询可能需要几秒甚至十几秒，如果同步等待会阻塞 HTTP 请求。
      异步方案：前端提交问题后立即返回一个任务 ID，然后定时轮询任务状态。

    状态流转：pending → running → done / failed / cancelled

    关键字段说明：
      - columns/rows：查询结果以 JSON 字符串存储（而非关联表），简化设计
      - pipeline_trace：AI 管道各步骤的执行轨迹（JSON 数组），用于调试和优化
      - intent：AI 识别的用户意图类型（如 DataQuery, Greeting 等）
    """
    __tablename__ = "async_queries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)  # 用户的自然语言问题
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | running | done | failed | cancelled
    generated_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI 生成的 SQL
    columns: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list — 列名列表
    rows: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list — 数据行列表
    row_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chart_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # AI 推荐的图表类型
    execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # DataQuery, etc.
    pipeline_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array of step events
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[str] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    messages: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    layout_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: grid settings, theme, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class DashboardShare(Base):
    __tablename__ = "dashboard_shares"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    share_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    password: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # bcrypt hashed
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class DashboardWidget(Base):
    __tablename__ = "dashboard_widgets"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    query_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    chart_type: Mapped[str] = mapped_column(String(50), default="table")
    columns: Mapped[str] = mapped_column(Text, nullable=True)  # JSON list
    rows: Mapped[str] = mapped_column(Text, nullable=True)  # JSON list
    row_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    position_x: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position_y: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())
