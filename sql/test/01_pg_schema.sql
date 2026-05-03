-- ChatBI 测试数据库初始化 (PostgreSQL)
-- 数据库: chatbi_test
-- 数据: 500 users, 32 categories, 200 products, 1000 orders, 2500 order_items,
--        847 payments, 318 shipping, 103 reviews, 20 coupons, 5 warehouses, 401 inventory
-- 运行: psql -U postgres -f 02_test_data_pg.sql

-- 创建数据库
-- SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = 'chatbi_test' AND pid <> pg_backend_pid();
-- DROP DATABASE IF EXISTS chatbi_test;
-- CREATE DATABASE chatbi_test;

\c chatbi_test

-- 11 张表 DDL
CREATE TABLE IF NOT EXISTS t_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(20),
    city VARCHAR(100),
    vip_level INT DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS t_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    parent_id INT DEFAULT NULL,
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS t_products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    category_id INT NOT NULL REFERENCES t_categories(id),
    price DECIMAL(10,2) NOT NULL,
    cost_price DECIMAL(10,2) NOT NULL,
    stock INT NOT NULL DEFAULT 0,
    weight DECIMAL(8,2),
    sku VARCHAR(50) UNIQUE,
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_products_category ON t_products(category_id);
CREATE INDEX idx_products_status ON t_products(status);

CREATE TABLE IF NOT EXISTS t_orders (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES t_users(id),
    order_no VARCHAR(50) UNIQUE NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    shipping_fee DECIMAL(10,2) DEFAULT 0,
    status VARCHAR(20) NOT NULL,
    city VARCHAR(100),
    note VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMP,
    shipped_at TIMESTAMP,
    delivered_at TIMESTAMP
);
CREATE INDEX idx_orders_user ON t_orders(user_id);
CREATE INDEX idx_orders_status ON t_orders(status);
CREATE INDEX idx_orders_created ON t_orders(created_at);

CREATE TABLE IF NOT EXISTS t_order_items (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    product_id INT NOT NULL REFERENCES t_products(id),
    quantity INT NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    subtotal DECIMAL(10,2) NOT NULL
);
CREATE INDEX idx_oi_order ON t_order_items(order_id);
CREATE INDEX idx_oi_product ON t_order_items(product_id);

CREATE TABLE IF NOT EXISTS t_payments (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    amount DECIMAL(10,2) NOT NULL,
    method VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'success',
    paid_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pay_order ON t_payments(order_id);

CREATE TABLE IF NOT EXISTS t_shipping (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    carrier VARCHAR(100),
    tracking_no VARCHAR(100),
    shipped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMP
);
CREATE INDEX idx_ship_order ON t_shipping(order_id);

CREATE TABLE IF NOT EXISTS t_reviews (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    product_id INT REFERENCES t_products(id),
    rating INT NOT NULL,
    content VARCHAR(1000),
    is_anonymous BOOLEAN DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_rev_order ON t_reviews(order_id);

CREATE TABLE IF NOT EXISTS t_coupons (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(200),
    discount_type VARCHAR(20) NOT NULL,
    discount_value DECIMAL(10,2) NOT NULL,
    min_order DECIMAL(10,2) DEFAULT 0,
    max_uses INT DEFAULT NULL,
    used_count INT DEFAULT 0,
    valid_from TIMESTAMP NOT NULL,
    valid_until TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS t_warehouses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    city VARCHAR(100),
    capacity INT,
    address VARCHAR(500),
    manager_name VARCHAR(100),
    manager_phone VARCHAR(20),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS t_inventory (
    id SERIAL PRIMARY KEY,
    product_id INT NOT NULL REFERENCES t_products(id),
    warehouse_id INT NOT NULL REFERENCES t_warehouses(id),
    quantity INT NOT NULL DEFAULT 0,
    reserved INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_inv_unique ON t_inventory(product_id, warehouse_id);

-- 数据通过 seed_pg_test.py 生成
-- 运行: .venv/bin/python seed_pg_test.py
