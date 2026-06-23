-- ChatBI v2 示例业务表 + 样本数据
-- 用途: T013 (数据源扫描) / T018 (复合指标) 的真实数据源
-- 库: njmind (元数据库，业务表用 biz_ 前缀避免和 ChatBI 系统表冲突)
-- 幂等: 可重复运行 (TRUNCATE + RESTART IDENTITY)
-- 对标: 海泰分析的电商场景 (orders/users/products + GMV/客单价)

-- ═══════ DDL ═══════

CREATE TABLE IF NOT EXISTS biz_users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(128) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(32),
    city VARCHAR(64),
    vip_level SMALLINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
COMMENT ON TABLE biz_users IS '用户表';
COMMENT ON COLUMN biz_users.username IS '用户名';
COMMENT ON COLUMN biz_users.email IS '邮箱';
COMMENT ON COLUMN biz_users.phone IS '手机号';
COMMENT ON COLUMN biz_users.city IS '所在城市';
COMMENT ON COLUMN biz_users.vip_level IS 'VIP等级(0-5)';
COMMENT ON COLUMN biz_users.created_at IS '注册时间';

CREATE TABLE IF NOT EXISTS biz_categories (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    parent_id BIGINT REFERENCES biz_categories(id),
    sort_order INT DEFAULT 0
);
COMMENT ON TABLE biz_categories IS '商品分类表';
COMMENT ON COLUMN biz_categories.name IS '分类名称';
COMMENT ON COLUMN biz_categories.parent_id IS '父分类ID';
COMMENT ON COLUMN biz_categories.sort_order IS '排序权重';

CREATE TABLE IF NOT EXISTS biz_products (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    category_id BIGINT REFERENCES biz_categories(id),
    price DECIMAL(10,2) NOT NULL,
    stock INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
COMMENT ON TABLE biz_products IS '商品表';
COMMENT ON COLUMN biz_products.name IS '商品名称';
COMMENT ON COLUMN biz_products.category_id IS '所属分类';
COMMENT ON COLUMN biz_products.price IS '售价';
COMMENT ON COLUMN biz_products.stock IS '库存数量';
COMMENT ON COLUMN biz_products.is_active IS '是否上架';

CREATE TABLE IF NOT EXISTS biz_orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES biz_users(id),
    order_no VARCHAR(64) NOT NULL UNIQUE,
    total_amount DECIMAL(12,2) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    payment_method VARCHAR(32),
    remark TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP,
    shipped_at TIMESTAMP
);
COMMENT ON TABLE biz_orders IS '订单表';
COMMENT ON COLUMN biz_orders.user_id IS '下单用户ID';
COMMENT ON COLUMN biz_orders.order_no IS '订单编号';
COMMENT ON COLUMN biz_orders.total_amount IS '订单总金额(含税)';
COMMENT ON COLUMN biz_orders.status IS '订单状态(pending/paid/shipped/cancelled)';
COMMENT ON COLUMN biz_orders.payment_method IS '支付方式';
COMMENT ON COLUMN biz_orders.created_at IS '下单时间';
COMMENT ON COLUMN biz_orders.paid_at IS '支付时间';
COMMENT ON COLUMN biz_orders.shipped_at IS '发货时间';

CREATE TABLE IF NOT EXISTS biz_order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES biz_orders(id),
    product_id BIGINT NOT NULL REFERENCES biz_products(id),
    quantity INT NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    subtotal DECIMAL(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED
);
COMMENT ON TABLE biz_order_items IS '订单明细表';
COMMENT ON COLUMN biz_order_items.order_id IS '所属订单ID';
COMMENT ON COLUMN biz_order_items.product_id IS '商品ID';
COMMENT ON COLUMN biz_order_items.quantity IS '购买数量';
COMMENT ON COLUMN biz_order_items.unit_price IS '成交单价';
COMMENT ON COLUMN biz_order_items.subtotal IS '小计金额';

-- 样本数据由 backend/scripts/seed_business_data.py 生成 (Python, 保证随机可复现)
