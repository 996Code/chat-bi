-- ChatBI 应用数据库初始化脚本 (PostgreSQL)
-- 数据库: PostgreSQL 16+
-- 用途: 生产/开发环境的应用层数据库表结构
-- 运行: psql -U postgres -f 01_chatbi_schema_pg.sql

-- 创建数据库（需要超级用户权限）
-- SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = 'chatbi' AND pid <> pg_backend_pid();
-- DROP DATABASE IF EXISTS chatbi;
-- CREATE DATABASE chatbi;

\c chatbi

-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    email_verified BOOLEAN DEFAULT false,
    failed_login_attempts INT DEFAULT 0,
    is_locked BOOLEAN DEFAULT false,
    lock_until TIMESTAMP NULL,
    role VARCHAR(50) DEFAULT 'user',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);
CREATE INDEX idx_users_tenant ON users(tenant_id);
CREATE INDEX idx_users_email ON users(email);

-- 数据源表
CREATE TABLE IF NOT EXISTS data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    name VARCHAR(200) NOT NULL,
    db_type VARCHAR(50) DEFAULT 'mysql',
    host VARCHAR(500) NOT NULL,
    port INT NOT NULL DEFAULT 3306,
    database_name VARCHAR(200) NOT NULL,
    username_encrypted VARCHAR(500) NOT NULL,
    password_encrypted VARCHAR(500) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    last_health_check TIMESTAMP NULL,
    health_check_error VARCHAR(500) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);
CREATE INDEX idx_ds_tenant ON data_sources(tenant_id);

-- 元数据配置表
CREATE TABLE IF NOT EXISTS metadata_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    datasource_id UUID NOT NULL,
    config TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);
CREATE INDEX idx_mc_tenant ON metadata_configs(tenant_id);
CREATE INDEX idx_mc_ds ON metadata_configs(datasource_id);

-- 审计日志表
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id VARCHAR(100),
    details TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_al_tenant ON audit_logs(tenant_id);
CREATE INDEX idx_al_user ON audit_logs(user_id);
CREATE INDEX idx_action ON audit_logs(action);

-- 保存的查询表
CREATE TABLE IF NOT EXISTS saved_queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    name VARCHAR(200) NOT NULL,
    query_text TEXT NOT NULL,
    generated_sql TEXT NOT NULL,
    datasource_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_sq_tenant ON saved_queries(tenant_id);
CREATE INDEX idx_sq_user ON saved_queries(user_id);

-- 反馈表
CREATE TABLE IF NOT EXISTS feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    query_id VARCHAR(100) NOT NULL,
    rating VARCHAR(20) NOT NULL,
    comment VARCHAR(1000) DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_fb_tenant ON feedback(tenant_id);
CREATE INDEX idx_fb_user ON feedback(user_id);

-- 分析事件表
CREATE TABLE IF NOT EXISTS analytics_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    event_name VARCHAR(100) NOT NULL,
    event_data TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ae_tenant ON analytics_events(tenant_id);
CREATE INDEX idx_ae_user ON analytics_events(user_id);

-- 会话表
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    datasource_id VARCHAR(100) NULL,
    title VARCHAR(200) DEFAULT '新对话',
    messages TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);
CREATE INDEX idx_cv_tenant ON conversations(tenant_id);
CREATE INDEX idx_cv_user ON conversations(user_id);
