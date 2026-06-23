"""
ChatBI v2 — Database Models

对标: v1 models.py — 所有核心业务模型
T004: Tenant/User/DataSource/SemanticModel/Conversation/AuditLog/SavedQuery/Feedback
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def new_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

class User(Base):
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

class DataSource(Base):
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ── Semantic Model ────────────────────────────────────────────

class SemanticModel(Base):
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

class Conversation(Base):
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

class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("conversations.id"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Audit Log ─────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ── Feedback ──────────────────────────────────────────────────

class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    saved_query_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("saved_queries.id"), nullable=True
    )
    feedback_type: Mapped[str] = mapped_column(
        Enum("like", "dislike", "sql_correction", "chart_correction", "comment",
             name="feedback_type"),
        nullable=False,
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corrected_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_chart: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str | None] = mapped_column(
        Enum("pending", "approved", "rejected", name="review_status"),
        default="pending",
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
