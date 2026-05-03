-- ChatBI Test Database Schema -- PostgreSQL
-- Run with: psql -U postgres -f 01_pg_schema.sql
-- Table names use t_ prefix as expected by the ChatBI application.

SELECT 'DROP DATABASE IF EXISTS chatbi_test' AS step;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = 'chatbi_test' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS chatbi_test;
CREATE DATABASE chatbi_test;

\c chatbi_test

-- Categories (3-level hierarchy)
CREATE TABLE t_categories (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    parent_id   BIGINT REFERENCES t_categories(id),
    level       INT NOT NULL DEFAULT 1,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Users
CREATE TABLE t_users (
    id          BIGSERIAL PRIMARY KEY,
    username    VARCHAR(100) NOT NULL,
    email       VARCHAR(255),
    phone       VARCHAR(20),
    vip_level   INT NOT NULL DEFAULT 0,
    city        VARCHAR(100) NOT NULL DEFAULT '北京',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Products
CREATE TABLE t_products (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    category_id BIGINT NOT NULL REFERENCES t_categories(id),
    price       NUMERIC(10,2) NOT NULL,
    cost_price  NUMERIC(10,2),
    stock       INT NOT NULL DEFAULT 0,
    sku         VARCHAR(100),
    weight      NUMERIC(8,2),
    status      VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Orders
CREATE TABLE t_orders (
    id              BIGSERIAL PRIMARY KEY,
    order_no        VARCHAR(50) NOT NULL UNIQUE,
    user_id         BIGINT NOT NULL REFERENCES t_users(id),
    total_amount    NUMERIC(10,2) NOT NULL,
    discount_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
    shipping_fee    NUMERIC(10,2) NOT NULL DEFAULT 0,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    city            VARCHAR(100),
    note            TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at         TIMESTAMP,
    shipped_at      TIMESTAMP,
    delivered_at    TIMESTAMP,
    cancelled_at    TIMESTAMP,
    refunded_at     TIMESTAMP
);

-- Order Items
CREATE TABLE t_order_items (
    id         BIGSERIAL PRIMARY KEY,
    order_id   BIGINT NOT NULL REFERENCES t_orders(id),
    product_id BIGINT NOT NULL REFERENCES t_products(id),
    quantity   INT NOT NULL,
    price      NUMERIC(10,2) NOT NULL,
    subtotal   NUMERIC(10,2) NOT NULL
);

-- Payments
CREATE TABLE t_payments (
    id         BIGSERIAL PRIMARY KEY,
    order_id   BIGINT NOT NULL REFERENCES t_orders(id),
    amount     NUMERIC(10,2) NOT NULL,
    method     VARCHAR(50) NOT NULL,
    status     VARCHAR(50) NOT NULL DEFAULT 'success',
    paid_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Shipping
CREATE TABLE t_shipping (
    id         BIGSERIAL PRIMARY KEY,
    order_id   BIGINT NOT NULL REFERENCES t_orders(id),
    carrier    VARCHAR(100),
    tracking_no VARCHAR(200),
    shipped_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMP
);

-- Reviews
CREATE TABLE t_reviews (
    id           BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL REFERENCES t_orders(id),
    product_id   BIGINT NOT NULL REFERENCES t_products(id),
    rating       INT NOT NULL,
    content      TEXT,
    is_anonymous BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Coupons
CREATE TABLE t_coupons (
    id             BIGSERIAL PRIMARY KEY,
    name           VARCHAR(100),
    code           VARCHAR(50) NOT NULL UNIQUE,
    discount_value NUMERIC(10,2) NOT NULL DEFAULT 0,
    discount_type  VARCHAR(20) NOT NULL DEFAULT 'fixed',
    min_order      NUMERIC(10,2) NOT NULL DEFAULT 0,
    max_uses       INT,
    used_count     INT NOT NULL DEFAULT 0,
    valid_from     TIMESTAMP NOT NULL,
    valid_until    TIMESTAMP NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Warehouses
CREATE TABLE t_warehouses (
    id            BIGSERIAL PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    city          VARCHAR(100) NOT NULL,
    address       VARCHAR(500),
    capacity      INT,
    manager_name  VARCHAR(100),
    manager_phone VARCHAR(20),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Inventory
CREATE TABLE t_inventory (
    id           BIGSERIAL PRIMARY KEY,
    product_id   BIGINT NOT NULL REFERENCES t_products(id),
    warehouse_id BIGINT NOT NULL REFERENCES t_warehouses(id),
    quantity     INT NOT NULL DEFAULT 0,
    reserved     INT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX uk_prod_wh ON t_inventory(product_id, warehouse_id);
