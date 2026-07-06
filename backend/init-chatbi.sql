-- ============================================================
-- ChatBI v2 — 数据库初始化脚本
--
-- 注意: 表结构由 SQLAlchemy 模型(models.py)自动管理, 启动时自动建表+补列。
-- 本脚本仅负责: 枚举类型 + 种子数据。不再手动定义表结构。
--
-- 用法:
--   psql -h 192.168.99.22 -U njmind -d njmind -f init-chatbi.sql
-- ============================================================

-- ── 扩展 ──────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 枚举类型 (启动时 auto_create_tables 也会补, 这里保留用于首次手动初始化) ──

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('admin', 'user', 'read_only');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE db_type AS ENUM ('mysql', 'postgresql');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE scan_status AS ENUM ('idle', 'scanning', 'done', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE audit_status AS ENUM ('success', 'fail', 'denied');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

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