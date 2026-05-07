import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, CheckConstraint, Text, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.types import GUID


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    email_verified: Mapped[bool] = mapped_column(default=False)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    is_locked: Mapped[bool] = mapped_column(default=False)
    lock_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="user")

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user', 'read_only')", name="ck_user_role"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    db_type: Mapped[str] = mapped_column(String(50), default="mysql")
    host: Mapped[str] = mapped_column(String(500), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False, default=3306)
    database_name: Mapped[str] = mapped_column(String(200), nullable=False)
    username_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    last_health_check: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    health_check_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class MetadataConfig(Base):
    __tablename__ = "metadata_configs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    config: Mapped[str] = mapped_column(Text, nullable=False)  # JSON schema content
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())


class MetadataConfigVersion(Base):
    __tablename__ = "metadata_config_versions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    config_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON snapshot at this version
    change_summary: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")
    sql_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)  # Total pipeline time
    sql_execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)  # Pure SQL execution time
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    success: Mapped[Optional[bool]] = mapped_column(nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chart_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    query_id: Mapped[str] = mapped_column(String(100), nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_data: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())


class AsyncQuery(Base):
    """Background query task — submitted asynchronously, polled for results."""
    __tablename__ = "async_queries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | running | done | failed | cancelled
    generated_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    columns: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    rows: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    row_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chart_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
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
