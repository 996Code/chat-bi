-- ChatBI Test Database Schema -- MySQL
-- Drop and recreate, then create tables for e-commerce test data.
-- Table names use t_ prefix as expected by the ChatBI application.

DROP DATABASE IF EXISTS chatbi_test;
CREATE DATABASE chatbi_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE chatbi_test;

-- Categories (3-level hierarchy)
CREATE TABLE t_categories (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    name        VARCHAR(100) NOT NULL,
    parent_id   BIGINT NULL,
    level       INT NOT NULL DEFAULT 1,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_cat_parent FOREIGN KEY (parent_id) REFERENCES t_categories(id)
) ENGINE=InnoDB;

-- Users
CREATE TABLE t_users (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    username    VARCHAR(100) NOT NULL,
    email       VARCHAR(255) NULL,
    phone       VARCHAR(20) NULL,
    vip_level   INT NOT NULL DEFAULT 0,
    city        VARCHAR(100) NOT NULL DEFAULT '北京',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Products
CREATE TABLE t_products (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    name        VARCHAR(200) NOT NULL,
    category_id BIGINT NOT NULL,
    price       DECIMAL(10,2) NOT NULL,
    cost_price  DECIMAL(10,2) NULL,
    stock       INT NOT NULL DEFAULT 0,
    sku         VARCHAR(100) NULL,
    weight      DECIMAL(8,2) NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_prod_cat FOREIGN KEY (category_id) REFERENCES t_categories(id)
) ENGINE=InnoDB;

-- Orders
CREATE TABLE t_orders (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_no        VARCHAR(50) NOT NULL UNIQUE,
    user_id         BIGINT NOT NULL,
    total_amount    DECIMAL(10,2) NOT NULL,
    discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
    shipping_fee    DECIMAL(10,2) NOT NULL DEFAULT 0,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    city            VARCHAR(100) NULL,
    note            TEXT NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at         DATETIME NULL,
    shipped_at      DATETIME NULL,
    delivered_at    DATETIME NULL,
    cancelled_at    DATETIME NULL,
    refunded_at     DATETIME NULL,
    CONSTRAINT fk_ord_user FOREIGN KEY (user_id) REFERENCES t_users(id)
) ENGINE=InnoDB;

-- Order Items
CREATE TABLE t_order_items (
    id         BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id   BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity   INT NOT NULL,
    price      DECIMAL(10,2) NOT NULL,
    subtotal   DECIMAL(10,2) NOT NULL,
    CONSTRAINT fk_item_ord FOREIGN KEY (order_id) REFERENCES t_orders(id),
    CONSTRAINT fk_item_prod FOREIGN KEY (product_id) REFERENCES t_products(id)
) ENGINE=InnoDB;

-- Payments
CREATE TABLE t_payments (
    id         BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id   BIGINT NOT NULL,
    amount     DECIMAL(10,2) NOT NULL,
    method     VARCHAR(50) NOT NULL,
    status     VARCHAR(50) NOT NULL DEFAULT 'success',
    paid_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_pay_ord FOREIGN KEY (order_id) REFERENCES t_orders(id)
) ENGINE=InnoDB;

-- Shipping
CREATE TABLE t_shipping (
    id         BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id   BIGINT NOT NULL,
    carrier    VARCHAR(100) NULL,
    tracking_no VARCHAR(200) NULL,
    shipped_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at DATETIME NULL,
    CONSTRAINT fk_ship_ord FOREIGN KEY (order_id) REFERENCES t_orders(id)
) ENGINE=InnoDB;

-- Reviews
CREATE TABLE t_reviews (
    id           BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id     BIGINT NOT NULL,
    product_id   BIGINT NOT NULL,
    rating       INT NOT NULL,
    content      TEXT NULL,
    is_anonymous TINYINT(1) NOT NULL DEFAULT 0,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rev_ord FOREIGN KEY (order_id) REFERENCES t_orders(id),
    CONSTRAINT fk_rev_prod FOREIGN KEY (product_id) REFERENCES t_products(id)
) ENGINE=InnoDB;

-- Coupons
CREATE TABLE t_coupons (
    id             BIGINT PRIMARY KEY AUTO_INCREMENT,
    name           VARCHAR(100) NULL,
    code           VARCHAR(50) NOT NULL UNIQUE,
    discount_value DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_type  VARCHAR(20) NOT NULL DEFAULT 'fixed',
    min_order      DECIMAL(10,2) NOT NULL DEFAULT 0,
    max_uses       INT NULL,
    used_count     INT NOT NULL DEFAULT 0,
    valid_from     DATETIME NOT NULL,
    valid_until    DATETIME NOT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Warehouses
CREATE TABLE t_warehouses (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    name          VARCHAR(200) NOT NULL,
    city          VARCHAR(100) NOT NULL,
    address       VARCHAR(500) NULL,
    capacity      INT NULL,
    manager_name  VARCHAR(100) NULL,
    manager_phone VARCHAR(20) NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Inventory
CREATE TABLE t_inventory (
    id           BIGINT PRIMARY KEY AUTO_INCREMENT,
    product_id   BIGINT NOT NULL,
    warehouse_id BIGINT NOT NULL,
    quantity     INT NOT NULL DEFAULT 0,
    reserved     INT NOT NULL DEFAULT 0,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_inv_prod FOREIGN KEY (product_id) REFERENCES t_products(id),
    CONSTRAINT fk_inv_wh FOREIGN KEY (warehouse_id) REFERENCES t_warehouses(id),
    UNIQUE KEY uk_prod_wh (product_id, warehouse_id)
) ENGINE=InnoDB;
