"""
ChatBI v2 — Database Models

对标: v1 models.py — 所有核心业务模型
T004: Tenant/User/DataSource/SemanticModel/Conversation/AuditLog/SavedQuery
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, declared_attr

from app.db.session import Base


def new_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Multi-tenant mixin (对标 v1 #48: 隔离应是默认行为) ────────
#
# TenantMixin 提供 tenant_filter() — 所有 tenant-scoped 模型继承它，
# 让查询代码用 Model.tenant_filter(tid) 显式过滤，而非手动写 .where(tenant_id==)。
# （自动注入的 session event 在 Phase 2 CRUD 时再加；这里先保证 filter API 可用）


class TenantMixin:
    """Mixin for tenant-scoped models. Provides tenant_filter() helper."""

    tenant_id: declared_attr

    @classmethod
    def tenant_filter(cls, tenant_id: str):
        """Return the SQLAlchemy filter for this tenant."""
        return cls.tenant_id == tenant_id


# ── Tenant ────────────────────────────────────────────────────

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


# ── User ──────────────────────────────────────────────────────

class User(TenantMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("admin", "user", "read_only", name="user_role"),
        default="user",
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")


# ── DataSource ────────────────────────────────────────────────

class DataSource(TenantMixin, Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    db_type: Mapped[str] = mapped_column(
        Enum("mysql", "postgresql", name="db_type"), nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 扫描状态 (对标 V1 metadata_sync_status + 经验教训 #25 异步任务模式)
    # scan_status: idle(未扫) / scanning(扫描中) / done(完成) / failed(失败)
    # 进度按阶段百分比更新, 前端轮询展示进度条 + 步骤文字
    scan_status: Mapped[str] = mapped_column(
        Enum("idle", "scanning", "done", "failed", name="scan_status"),
        default="idle", nullable=False, index=True,
    )
    scan_progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    scan_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 当前步骤文字
    scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ── Semantic Model ────────────────────────────────────────────

class SemanticModel(TenantMixin, Base):
    """Versioned semantic layer definition (JSON)."""
    __tablename__ = "semantic_models"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    data_source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_sources.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "data_source_id", "version", name="uq_semantic_version"),
    )


# ── Conversation ──────────────────────────────────────────────

class Conversation(TenantMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="New Conversation")
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ── Saved Query ───────────────────────────────────────────────

class SavedQuery(TenantMixin, Base):
    __tablename__ = "saved_queries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "data_source_id", "question", "sql_text", name="uq_saved_query_content"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    data_source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_sources.id"), nullable=False, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Dashboard (看板: V1 完整复刻) ──────────────────────────────

class Dashboard(TenantMixin, Base):
    """看板 (对标 V1 Dashboard)。"""
    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DashboardWidget(TenantMixin, Base):
    """看板组件 (对标 V1 DashboardWidget)。"""
    __tablename__ = "dashboard_widgets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    dashboard_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("dashboards.id"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    query_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    datasource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    chart_type: Mapped[str] = mapped_column(String(50), default="table")
    # 实时查询模式: 不存结果快照, columns/rows 保留字段但实时查询时不入库
    columns: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 图表配置缓存 (LLM 生成的 chart_type/dim_col/measure_cols 映射)
    # refresh 时用缓存的列映射 + 实时数据 inject_data, 避免每次重跑 LLM
    chart_option: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 布局
    position_x: Mapped[int] = mapped_column(Integer, default=0)
    position_y: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=6)
    height: Mapped[int] = mapped_column(Integer, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ── Audit Log ─────────────────────────────────────────────────

class AuditLog(TenantMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # tenant_id 不设 FK: 审计是安全/取证记录, 未认证事件 (登录失败/暴力破解) 无合法租户,
    # 仍必须记录。FK 反成单一故障点 (一个 bad tenant_id 就能让整条审计链失败, 违背 fail-closed)。
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("success", "fail", "denied", name="audit_status"), nullable=False
    )
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sql_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # DSO-07: 慢查询标记 (SQL 执行耗时 + 是否慢查询, 可配置阈值判定)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_slow: Mapped[bool] = mapped_column(Boolean, default=False)
    # DSO-05: 数据源归属 (按源聚合统计用, nullable 兼容旧记录)
    data_source_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )