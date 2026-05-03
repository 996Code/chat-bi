-- ChatBI 应用数据库初始化脚本
-- 数据库: MySQL 9.4
-- 用途: 生产/开发环境的应用层数据库表结构
-- 运行: mysql -u root -p < 01_chatbi_schema.sql

CREATE DATABASE IF NOT EXISTS chatbi DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE chatbi;

-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NULL DEFAULT NOW() ON UPDATE NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active TINYINT(1) DEFAULT 1,
    email_verified TINYINT(1) DEFAULT 0,
    failed_login_attempts INT DEFAULT 0,
    is_locked TINYINT(1) DEFAULT 0,
    lock_until DATETIME NULL,
    role VARCHAR(50) DEFAULT 'user',
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NULL DEFAULT NOW() ON UPDATE NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_email (email),
    UNIQUE KEY uk_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 数据源表
CREATE TABLE IF NOT EXISTS data_sources (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    name VARCHAR(200) NOT NULL,
    db_type VARCHAR(50) DEFAULT 'mysql',
    host VARCHAR(500) NOT NULL,
    port INT NOT NULL DEFAULT 3306,
    database_name VARCHAR(200) NOT NULL,
    username_encrypted VARCHAR(500) NOT NULL,
    password_encrypted VARCHAR(500) NOT NULL,
    is_active TINYINT(1) DEFAULT 1,
    last_health_check DATETIME NULL,
    health_check_error VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NULL DEFAULT NOW() ON UPDATE NOW(),
    INDEX idx_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 元数据配置表（语义层模型）
CREATE TABLE IF NOT EXISTS metadata_configs (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    datasource_id CHAR(36) NOT NULL,
    config TEXT NOT NULL COMMENT 'JSON schema content',
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NULL DEFAULT NOW() ON UPDATE NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_datasource (datasource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 审计日志表
CREATE TABLE IF NOT EXISTS audit_logs (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    user_id CHAR(36) NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id VARCHAR(100),
    details TEXT,
    created_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_user (user_id),
    INDEX idx_action (action),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 保存的查询表
CREATE TABLE IF NOT EXISTS saved_queries (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    name VARCHAR(200) NOT NULL,
    query_text TEXT NOT NULL,
    generated_sql TEXT NOT NULL,
    datasource_id CHAR(36) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_user (user_id),
    INDEX idx_datasource (datasource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 反馈表
CREATE TABLE IF NOT EXISTS feedback (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    query_id VARCHAR(100) NOT NULL,
    rating VARCHAR(20) NOT NULL,
    comment VARCHAR(1000) DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_user (user_id),
    INDEX idx_rating (rating)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 分析事件表
CREATE TABLE IF NOT EXISTS analytics_events (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    event_name VARCHAR(100) NOT NULL,
    event_data TEXT,
    created_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_user (user_id),
    INDEX idx_event (event_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 会话表
CREATE TABLE IF NOT EXISTS conversations (
    id CHAR(36) PRIMARY KEY,
    tenant_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    datasource_id VARCHAR(100) NULL,
    title VARCHAR(200) DEFAULT '新对话',
    messages TEXT COMMENT 'JSON array',
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NULL DEFAULT NOW() ON UPDATE NOW(),
    INDEX idx_tenant (tenant_id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
