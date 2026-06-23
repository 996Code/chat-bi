-- ============================================================
-- ChatBI v2 — 数据库初始化脚本
--
-- 用法:
--   psql -h 192.168.99.22 -U njmind -d njmind -f init-chatbi.sql
-- ============================================================

-- ── 扩展 ──────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 枚举类型 ──────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('admin', 'user', 'read_only');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE db_type AS ENUM ('mysql', 'postgresql');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE audit_status AS ENUM ('success', 'fail', 'denied');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE feedback_type AS ENUM ('like', 'dislike', 'sql_correction', 'chart_correction', 'comment');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE review_status AS ENUM ('pending', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── 表 ────────────────────────────────────────────────────

-- 租户
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 用户
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(128) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role user_role DEFAULT 'user' NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    email_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- 数据源
CREATE TABLE IF NOT EXISTS data_sources (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    name VARCHAR(128) NOT NULL,
    db_type db_type NOT NULL,
    host VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL,
    database VARCHAR(128) NOT NULL,
    username VARCHAR(128) NOT NULL,
    encrypted_password VARCHAR(512) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_datasources_tenant ON data_sources(tenant_id);

-- 语义模型（版本化）
CREATE TABLE IF NOT EXISTS semantic_models (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    data_source_id VARCHAR(32) NOT NULL REFERENCES data_sources(id),
    version INTEGER NOT NULL DEFAULT 1,
    content JSONB NOT NULL,
    is_current BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, data_source_id, version)
);
CREATE INDEX IF NOT EXISTS idx_semantic_tenant ON semantic_models(tenant_id);
CREATE INDEX IF NOT EXISTS idx_semantic_datasource ON semantic_models(data_source_id);

-- 对话
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(32) NOT NULL REFERENCES users(id),
    title VARCHAR(255) DEFAULT 'New Conversation',
    state_json JSONB,
    is_archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_tenant ON conversations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);

-- 保存的查询
CREATE TABLE IF NOT EXISTS saved_queries (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(32) NOT NULL REFERENCES users(id),
    conversation_id VARCHAR(32) REFERENCES conversations(id),
    question TEXT NOT NULL,
    sql_text TEXT NOT NULL,
    result_summary TEXT,
    chart_config JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sq_tenant ON saved_queries(tenant_id);

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(32) REFERENCES users(id),
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(32),
    action VARCHAR(64) NOT NULL,
    status audit_status NOT NULL,
    detail JSONB,
    sql_text TEXT,
    error_message TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

-- 反馈
CREATE TABLE IF NOT EXISTS feedback (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(32) NOT NULL REFERENCES users(id),
    saved_query_id VARCHAR(32) REFERENCES saved_queries(id),
    feedback_type feedback_type NOT NULL,
    rating INTEGER,
    corrected_sql TEXT,
    corrected_chart JSONB,
    comment TEXT,
    is_reviewed BOOLEAN DEFAULT FALSE,
    review_status review_status DEFAULT 'pending',
    reviewed_by VARCHAR(32) REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_tenant ON feedback(tenant_id);

-- ── 种子数据 ──────────────────────────────────────────────

-- 默认租户
INSERT INTO tenants (id, name) VALUES
    ('default_tenant_0000000000000001', '默认租户')
ON CONFLICT (id) DO NOTHING;

-- 管理员账号
-- 密码: 1992810Yjt
INSERT INTO users (id, tenant_id, email, username, hashed_password, role, is_active, email_verified) VALUES
    ('admin_user_00000000000000000001', 'default_tenant_0000000000000001',
     'admin@chatbi.local', 'admin',
     '$2b$12$ygAsJc0Qag7Sq8AJlCqGxOpS.jOc8.EhpGNVTHxsgEc36REuKLOv.',
     'admin', TRUE, TRUE)
ON CONFLICT (id) DO NOTHING;

-- ── 完成 ──────────────────────────────────────────────────
SELECT 'ChatBI v2 数据库初始化完成' AS status;
SELECT id, username, email, role FROM users;