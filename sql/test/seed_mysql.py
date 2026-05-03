"""Seed complex test data into MySQL `chatbi_test` and PostgreSQL `chatbi_test`.
读取 tests/test_config.json 获取数据库连接配置，支持切换服务器后直接运行。
"""
import asyncio
import json
import os
import random
import string
from datetime import datetime, timedelta, timezone

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "tests", "test_config.json")
with open(CONFIG_PATH) as f:
    DB_CONFIG = json.load(f)

MYSQL_HOST = DB_CONFIG["mysql"]["host"]
MYSQL_PORT = DB_CONFIG["mysql"]["port"]
MYSQL_USER = DB_CONFIG["mysql"]["user"]
MYSQL_PASSWORD = DB_CONFIG["mysql"]["password"]
MYSQL_TEST_DB = DB_CONFIG["mysql"]["test_db"]

PG_HOST = DB_CONFIG["postgresql"]["host"]
PG_PORT = DB_CONFIG["postgresql"]["port"]
PG_USER = DB_CONFIG["postgresql"]["user"]
PG_PASSWORD = DB_CONFIG["postgresql"]["password"]
PG_ADMIN_DB = DB_CONFIG["postgresql"]["admin_db"]
PG_TEST_DB = DB_CONFIG["postgresql"]["test_db"]

random.seed(42)

NOW = datetime.now(timezone.utc)
CITIES = ["杭州", "上海", "北京", "深圳", "广州", "成都", "南京", "武汉", "西安", "重庆"]
CATEGORIES = ["电子产品", "服装", "食品", "家居", "美妆", "运动", "图书", "母婴"]
ORDER_STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled", "refunded"]
PAYMENT_METHODS = ["alipay", "wechat", "bank_card", "huabei"]
SHIPPING_CARRIERS = ["顺丰", "中通", "圆通", "韵达", "EMS"]
PRODUCT_NAMES = [
    "iPhone 16 Pro", "MacBook Air M4", "AirPods Pro", "小米15 Ultra",
    "华为 Mate 70", "索尼 WH-1000XM5", "Switch OLED", "戴森 V15",
    "SK-II 神仙水", "雅诗兰黛小棕瓶", "优衣库 羽绒服", "Nike Air Max",
    "三只松鼠坚果", "三只松鼠薯片", "良品铺子肉松饼", "三只松鼠芒果干",
    "乐高 星球大战", "乐高 城市系列", "小米扫地机器人", "戴森吹风机",
    "Nike 运动跑鞋", "Adidas 篮球鞋", "李宁 乒乓球拍", "安踏 运动外套",
    "海飞丝洗发露", "欧莱雅面霜", "完美日记口红", "花西子散粉",
    "《算法导论》", "《三体》", "《百年孤独》", "《人类简史》",
    "婴儿纸尿裤", "婴儿奶粉", "儿童积木", "儿童绘本",
    "智能手表", "平板电脑", "蓝牙音箱", "电动牙刷",
]


def rand_date(start, end):
    delta = int((end - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, delta))


def rand_choice(items):
    return random.choice(items)


MYSQL_DDL = """
CREATE TABLE IF NOT EXISTS t_users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(20),
    city VARCHAR(100),
    vip_level INT DEFAULT 0 COMMENT '0=普通,1=银卡,2=金卡,3=钻石',
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NOT NULL DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_categories (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    parent_id BIGINT DEFAULT NULL,
    sort_order INT DEFAULT 0,
    is_active TINYINT(1) DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_products (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL COMMENT '商品名称',
    category_id BIGINT NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    cost_price DECIMAL(10,2) NOT NULL,
    stock INT NOT NULL DEFAULT 0,
    weight DECIMAL(8,2) COMMENT '重量(kg)',
    sku VARCHAR(50) UNIQUE,
    status VARCHAR(20) DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT NOW(),
    updated_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_category (category_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    order_no VARCHAR(50) UNIQUE NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    shipping_fee DECIMAL(10,2) DEFAULT 0,
    status VARCHAR(20) NOT NULL,
    city VARCHAR(100),
    note VARCHAR(500),
    created_at DATETIME NOT NULL DEFAULT NOW(),
    paid_at DATETIME,
    shipped_at DATETIME,
    delivered_at DATETIME,
    INDEX idx_user (user_id),
    INDEX idx_status (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_order_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    price DECIMAL(10,2) NOT NULL COMMENT '下单时单价',
    subtotal DECIMAL(10,2) NOT NULL,
    INDEX idx_order (order_id),
    INDEX idx_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_payments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    method VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'success',
    paid_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_order (order_id),
    INDEX idx_method (method)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_shipping (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    carrier VARCHAR(100),
    tracking_no VARCHAR(100),
    shipped_at DATETIME NOT NULL DEFAULT NOW(),
    delivered_at DATETIME,
    INDEX idx_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_reviews (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT,
    rating INT NOT NULL COMMENT '1-5',
    content VARCHAR(1000),
    is_anonymous TINYINT(1) DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_order (order_id),
    INDEX idx_rating (rating)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_coupons (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(200),
    discount_type VARCHAR(20) NOT NULL COMMENT 'fixed/percent',
    discount_value DECIMAL(10,2) NOT NULL,
    min_order DECIMAL(10,2) DEFAULT 0,
    max_uses INT DEFAULT NULL,
    used_count INT DEFAULT 0,
    valid_from DATETIME NOT NULL,
    valid_until DATETIME NOT NULL,
    is_active TINYINT(1) DEFAULT 1,
    INDEX idx_code (code),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_warehouses (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    city VARCHAR(100),
    capacity INT,
    address VARCHAR(500),
    manager_name VARCHAR(100),
    manager_phone VARCHAR(20),
    is_active TINYINT(1) DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_inventory (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    product_id BIGINT NOT NULL,
    warehouse_id BIGINT NOT NULL,
    quantity INT NOT NULL DEFAULT 0,
    reserved INT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT NOW(),
    UNIQUE KEY uk_product_warehouse (product_id, warehouse_id),
    INDEX idx_product (product_id),
    INDEX idx_warehouse (warehouse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

PG_DDL = """
CREATE TABLE IF NOT EXISTS t_users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(20),
    city VARCHAR(100),
    vip_level INT DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS t_categories (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    parent_id BIGINT DEFAULT NULL,
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS t_products (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    category_id BIGINT NOT NULL REFERENCES t_categories(id),
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
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES t_users(id),
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
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES t_orders(id),
    product_id BIGINT NOT NULL REFERENCES t_products(id),
    quantity INT NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    subtotal DECIMAL(10,2) NOT NULL
);
CREATE INDEX idx_order_items_order ON t_order_items(order_id);
CREATE INDEX idx_order_items_product ON t_order_items(product_id);

CREATE TABLE IF NOT EXISTS t_payments (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES t_orders(id),
    amount DECIMAL(10,2) NOT NULL,
    method VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'success',
    paid_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_payments_order ON t_payments(order_id);
CREATE INDEX idx_payments_method ON t_payments(method);

CREATE TABLE IF NOT EXISTS t_shipping (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES t_orders(id),
    carrier VARCHAR(100),
    tracking_no VARCHAR(100),
    shipped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMP
);
CREATE INDEX idx_shipping_order ON t_shipping(order_id);

CREATE TABLE IF NOT EXISTS t_reviews (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES t_orders(id),
    product_id BIGINT REFERENCES t_products(id),
    rating INT NOT NULL,
    content VARCHAR(1000),
    is_anonymous BOOLEAN DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_reviews_order ON t_reviews(order_id);
CREATE INDEX idx_reviews_rating ON t_reviews(rating);

CREATE TABLE IF NOT EXISTS t_coupons (
    id BIGSERIAL PRIMARY KEY,
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
CREATE INDEX idx_coupons_code ON t_coupons(code);
CREATE INDEX idx_coupons_active ON t_coupons(is_active);

CREATE TABLE IF NOT EXISTS t_warehouses (
    id BIGSERIAL PRIMARY KEY,
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
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES t_products(id),
    warehouse_id BIGINT NOT NULL REFERENCES t_warehouses(id),
    quantity INT NOT NULL DEFAULT 0,
    reserved INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_inv_unique ON t_inventory(product_id, warehouse_id);
CREATE INDEX idx_inv_product ON t_inventory(product_id);
CREATE INDEX idx_inv_warehouse ON t_inventory(warehouse_id);
"""


def gen_mysql_data():
    start = NOW - timedelta(days=180)
    sqls = []

    # 500 users
    for i in range(500):
        city = rand_choice(CITIES)
        vip = random.choices([0, 1, 2, 3], weights=[50, 25, 15, 10])[0]
        created = rand_date(start, NOW)
        name = f"user_{i+1:04d}_{random.choice(['张','李','王','赵','刘','陈','杨','黄','周','吴'])}"
        email = f"user{i+1}@test.com"
        phone = f"1{''.join([str(random.randint(0,9)) for _ in range(10)])}"
        sqls.append(
            f"INSERT INTO t_users (username, email, phone, city, vip_level, created_at, updated_at) "
            f"VALUES ('{name}', '{email}', '{phone}', '{city}', {vip}, "
            f"'{created.strftime('%Y-%m-%d %H:%M:%S')}', '{created.strftime('%Y-%m-%d %H:%M:%S')}')"
        )

    # 8 categories + subcategories
    cat_sqls = []
    cat_id = 0
    for cat in CATEGORIES:
        cat_id += 1
        cat_sqls.append(
            f"INSERT INTO t_categories (id, name, parent_id, sort_order, is_active, created_at) "
            f"VALUES ({cat_id}, '{cat}', NULL, {cat_id}, 1, '{NOW.strftime('%Y-%m-%d %H:%M:%S')}')"
        )
        for j, sub in enumerate(["标准款", "升级款", "旗舰款"]):
            cat_id += 1
            cat_sqls.append(
                f"INSERT INTO t_categories (id, name, parent_id, sort_order, is_active, created_at) "
                f"VALUES ({cat_id}, '{cat}-{sub}', {j+1}, {cat_id}, 1, '{NOW.strftime('%Y-%m-%d %H:%M:%S')}')"
            )
    sqls.extend(cat_sqls)

    # 200 products
    prod_prices = []
    for i in range(200):
        name = PRODUCT_NAMES[i % len(PRODUCT_NAMES)]
        cat = random.randint(1, 8)
        price = round(random.uniform(9.9, 9999.0), 2)
        cost = round(price * random.uniform(0.3, 0.7), 2)
        stock = random.randint(0, 5000)
        weight = round(random.uniform(0.05, 15.0), 2)
        sku = f"SKU-{i+1:05d}"
        status = random.choices(["active", "inactive", "discontinued"], weights=[80, 15, 5])[0]
        created = rand_date(start, NOW)
        prod_prices.append(price)
        sqls.append(
            f"INSERT INTO t_products (id, name, category_id, price, cost_price, stock, weight, sku, status, created_at, updated_at) "
            f"VALUES ({i+1}, '{name}', {cat}, {price}, {cost}, {stock}, {weight}, '{sku}', '{status}', "
            f"'{created.strftime('%Y-%m-%d %H:%M:%S')}', '{created.strftime('%Y-%m-%d %H:%M:%S')}')"
        )

    # 1000 orders
    order_ids = []
    for i in range(1000):
        uid = random.randint(1, 500)
        order_no = f"ORD{NOW.strftime('%Y%m%d')}{i+1:06d}"
        status = rand_choice(ORDER_STATUSES)
        city = rand_choice(CITIES)
        total = round(random.uniform(50, 20000), 2)
        discount = round(total * random.uniform(0, 0.15), 2)
        fee = round(random.choice([0, 0, 0, 5, 8, 12, 15]), 2)
        created = rand_date(start, NOW)
        paid = created + timedelta(minutes=random.randint(1, 120)) if status != "pending" else None
        shipped = paid + timedelta(hours=random.randint(2, 48)) if status in ("shipped", "delivered") else None
        delivered = shipped + timedelta(days=random.randint(1, 7)) if status == "delivered" else None
        order_ids.append((i+1, total, status, created, paid))

        paid_s = f"'{paid.strftime('%Y-%m-%d %H:%M:%S')}'" if paid else "NULL"
        shipped_s = f"'{shipped.strftime('%Y-%m-%d %H:%M:%S')}'" if shipped else "NULL"
        delivered_s = f"'{delivered.strftime('%Y-%m-%d %H:%M:%S')}'" if delivered else "NULL"
        note = f"测试订单{i+1}" if random.random() < 0.1 else ""

        sqls.append(
            f"INSERT INTO t_orders (id, user_id, order_no, total_amount, discount_amount, shipping_fee, status, city, note, "
            f"created_at, paid_at, shipped_at, delivered_at) "
            f"VALUES ({i+1}, {uid}, '{order_no}', {total}, {discount}, {fee}, '{status}', '{city}', '{note}', "
            f"'{created.strftime('%Y-%m-%d %H:%M:%S')}', {paid_s}, {shipped_s}, {delivered_s})"
        )

    # 2500 order items
    for i in range(2500):
        oid = random.randint(1, 1000)
        pid = random.randint(1, 200)
        qty = random.randint(1, 10)
        price = round(prod_prices[(pid-1) % len(prod_prices)] * random.uniform(0.8, 1.2), 2)
        subtotal = round(qty * price, 2)
        sqls.append(
            f"INSERT INTO t_order_items (id, order_id, product_id, quantity, price, subtotal) "
            f"VALUES ({i+1}, {oid}, {pid}, {qty}, {price}, {subtotal})"
        )

    # Payments for non-pending orders
    pay_id = 0
    for oid, total, status, created, paid in order_ids:
        if status == "pending":
            continue
        pay_id += 1
        method = rand_choice(PAYMENT_METHODS)
        tid = f"TXN{''.join(random.choices(string.digits, k=12))}"
        paid_at = paid or created + timedelta(minutes=5)
        sqls.append(
            f"INSERT INTO t_payments (id, order_id, amount, method, transaction_id, status, paid_at) "
            f"VALUES ({pay_id}, {oid}, {total}, '{method}', '{tid}', 'success', "
            f"'{paid_at.strftime('%Y-%m-%d %H:%M:%S')}')"
        )

    # Shipping for shipped/delivered orders
    ship_id = 0
    for oid, total, status, created, paid in order_ids:
        if status not in ("shipped", "delivered"):
            continue
        ship_id += 1
        carrier = rand_choice(SHIPPING_CARRIERS)
        tracking = f"{carrier[:1]}{''.join(random.choices(string.digits, k=12))}"
        shipped = paid + timedelta(hours=random.randint(2, 48))
        delivered = shipped + timedelta(days=random.randint(1, 7)) if status == "delivered" else None
        delivered_s = f"'{delivered.strftime('%Y-%m-%d %H:%M:%S')}'" if delivered else "NULL"
        sqls.append(
            f"INSERT INTO t_shipping (id, order_id, carrier, tracking_no, shipped_at, delivered_at) "
            f"VALUES ({ship_id}, {oid}, '{carrier}', '{tracking}', "
            f"'{shipped.strftime('%Y-%m-%d %H:%M:%S')}', {delivered_s})"
        )

    # Reviews for delivered orders
    review_id = 0
    for oid, total, status, created, paid in order_ids:
        if status != "delivered" or random.random() < 0.4:
            continue
        review_id += 1
        pid = random.randint(1, 200)
        rating = random.choices([1, 2, 3, 4, 5], weights=[5, 5, 10, 30, 50])[0]
        content = random.choice(["很好", "不错", "一般", "质量可以", "推荐购买", "物流快", "包装好", "性价比很高"])
        anon = 1 if random.random() < 0.2 else 0
        created_at = delivered or (paid + timedelta(days=5))
        sqls.append(
            f"INSERT INTO t_reviews (id, order_id, product_id, rating, content, is_anonymous, created_at) "
            f"VALUES ({review_id}, {oid}, {pid}, {rating}, '{content}', {anon}, "
            f"'{created_at.strftime('%Y-%m-%d %H:%M:%S')}')"
        )

    # 20 coupons
    coupon_types = [("fixed", 10), ("fixed", 20), ("fixed", 50), ("percent", 0.9), ("percent", 0.85), ("percent", 0.7)]
    for i in range(20):
        dtype, dval = random.choice(coupon_types)
        if dtype == "percent":
            code = f"SALE{int(dval*100)}_{i:02d}"
            name = f"{int((1-dval)*100)}%折扣券"
        else:
            code = f"OFF{dval}_{i:02d}"
            name = f"{dval}元优惠券"
        min_order = round(dval * random.uniform(2, 10))
        max_uses = random.choice([100, 500, 1000, None])
        used = random.randint(0, 200)
        valid_from = start + timedelta(days=i*5)
        valid_until = valid_from + timedelta(days=60)
        max_s = str(max_uses) if max_uses else "NULL"
        is_active = 1 if random.random() < 0.7 else 0
        sqls.append(
            f"INSERT INTO t_coupons (id, code, name, discount_type, discount_value, min_order, max_uses, used_count, "
            f"valid_from, valid_until, is_active) "
            f"VALUES ({i+1}, '{code}', '{name}', '{dtype}', {dval}, {min_order}, {max_s}, {used}, "
            f"'{valid_from.strftime('%Y-%m-%d %H:%M:%S')}', '{valid_until.strftime('%Y-%m-%d %H:%M:%S')}', {is_active})"
        )

    # 5 warehouses
    warehouses = [
        ("华东仓", "上海", 50000, "上海市浦东新区XX路100号", "张经理", "13800000001"),
        ("华南仓", "广州", 40000, "广州市白云区XX路200号", "李经理", "13800000002"),
        ("华北仓", "北京", 35000, "北京市大兴区XX路300号", "王经理", "13800000003"),
        ("西南仓", "成都", 25000, "成都市双流区XX路400号", "赵经理", "13800000004"),
        ("华中仓", "武汉", 30000, "武汉市江夏区XX路500号", "刘经理", "13800000005"),
    ]
    for i, (name, city, cap, addr, mgr, phone) in enumerate(warehouses):
        sqls.append(
            f"INSERT INTO t_warehouses (id, name, city, capacity, address, manager_name, manager_phone, is_active, created_at) "
            f"VALUES ({i+1}, '{name}', '{city}', {cap}, '{addr}', '{mgr}', '{phone}', 1, '{NOW.strftime('%Y-%m-%d %H:%M:%S')}')"
        )

    # 500 inventory records
    for i in range(500):
        pid = random.randint(1, 200)
        wid = random.randint(1, 5)
        qty = random.randint(0, 2000)
        reserved = random.randint(0, min(qty, 100))
        sqls.append(
            f"INSERT IGNORE INTO t_inventory (product_id, warehouse_id, quantity, reserved, updated_at) "
            f"VALUES ({pid}, {wid}, {qty}, {reserved}, '{NOW.strftime('%Y-%m-%d %H:%M:%S')}')"
        )

    return sqls


def gen_pg_data():
    """Generate PostgreSQL data with the same schema."""
    mysql_sqls = gen_mysql_data()
    pg_sqls = []

    # Convert MySQL syntax to PostgreSQL
    for sql in mysql_sqls:
        s = sql
        # Replace datetime format
        s = s.replace("TINYINT(1)", "BOOLEAN")
        s = s.replace("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", "")
        # Remove INDEX, UNIQUE KEY lines (handled separately)
        if s.strip().startswith("INDEX ") or s.strip().startswith("UNIQUE KEY "):
            continue
        s = s.replace("'NULL'", "NULL")
        # Replace boolean values
        s = s.replace(", 1,", ", true,").replace(", 0,", ", false,")
        s = s.replace("= 1)", "= true)").replace("= 0)", "= false)")
        pg_sqls.append(s)

    return pg_sqls


async def seed_mysql():
    import aiomysql
    print("=== Seeding MySQL: chatbi_test ===")

    # Create database
    conn = await aiomysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD)
    async with conn.cursor() as cur:
        await cur.execute("DROP DATABASE IF EXISTS chatbi_test")
        await cur.execute("CREATE DATABASE chatbi_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.close()
    print("  Database created")

    # Connect and create tables
    conn = await aiomysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, db=MYSQL_TEST_DB)
    async with conn.cursor() as cur:
        for stmt in MYSQL_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await cur.execute(stmt)
        await conn.commit()
    print("  Tables created")

    # Insert data
    sqls = gen_mysql_data()
    async with conn.cursor() as cur:
        # Disable foreign key checks for bulk insert
        await cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for i, sql in enumerate(sqls):
            try:
                await cur.execute(sql)
            except Exception as e:
                print(f"  ERROR at SQL #{i+1}: {e}")
                print(f"  SQL: {sql[:200]}")
        await cur.execute("SET FOREIGN_KEY_CHECKS=1")
        await conn.commit()
    conn.close()
    print(f"  Data inserted: {len(sqls)} statements")

    # Verify
    conn = await aiomysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, db=MYSQL_TEST_DB)
    async with conn.cursor() as cur:
        tables = ["t_users", "t_categories", "t_products", "t_orders", "t_order_items",
                   "t_payments", "t_shipping", "t_reviews", "t_coupons", "t_warehouses", "t_inventory"]
        for t in tables:
            await cur.execute(f"SELECT COUNT(*) FROM {t}")
            cnt = (await cur.fetchone())[0]
            print(f"  {t}: {cnt} rows")
    conn.close()
    print("  MySQL seed completed successfully\n")


async def seed_postgresql():
    import asyncpg
    print("=== Seeding PostgreSQL: chatbi_test ===")

    # Connect to postgres admin
    conn = await asyncpg.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, database=PG_ADMIN_DB)

    # Drop and create database (need to close other connections first)
    try:
        await conn.execute("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{PG_TEST_DB}'
            AND pid <> pg_backend_pid()
        """)
    except Exception:
        pass
    await conn.execute("DROP DATABASE IF EXISTS chatbi_test")
    await conn.execute("CREATE DATABASE chatbi_test")
    await conn.close()
    print("  Database created")

    # Connect to new database
    conn = await asyncpg.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, database=PG_TEST_DB)

    # Create tables
    for stmt in PG_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            try:
                await conn.execute(stmt)
            except Exception as e:
                print(f"  WARN DDL: {e}")
                print(f"  SQL: {stmt[:200]}")
    print("  Tables created")

    # Insert data
    mysql_sqls = gen_mysql_data()
    pg_sqls = []
    for sql in mysql_sqls:
        # Convert to PG syntax
        s = sql
        s = s.replace("TINYINT(1)", "BOOLEAN")
        s = s.replace("'NULL'", "NULL")
        # MySQL boolean 0/1 in VALUES
        # Handle common patterns
        s = s.replace(", 1, ", ", true, ").replace(", 0, ", ", false, ")
        s = s.replace(", 1)", ", true)").replace(", 0)", ", false)")
        # INSERT IGNORE -> normal INSERT (we'll handle errors)
        s = s.replace("INSERT IGNORE INTO", "INSERT INTO")
        # Auto-increment: remove explicit id for SERIAL columns
        pg_sqls.append(s)

    for i, sql in enumerate(pg_sqls):
        try:
            await conn.execute(sql)
        except Exception as e:
            print(f"  ERROR at SQL #{i+1}: {e}")
            if "duplicate key" not in str(e).lower():
                print(f"  SQL: {sql[:200]}")
    print(f"  Data inserted: {len(pg_sqls)} statements")

    # Verify
    tables = ["t_users", "t_categories", "t_products", "t_orders", "t_order_items",
              "t_payments", "t_shipping", "t_reviews", "t_coupons", "t_warehouses", "t_inventory"]
    for t in tables:
        r = await conn.fetchrow(f"SELECT COUNT(*) as cnt FROM {t}")
        print(f"  {t}: {r['cnt']} rows")
    await conn.close()
    print("  PostgreSQL seed completed successfully\n")


async def main():
    await seed_mysql()
    await seed_postgresql()


if __name__ == "__main__":
    asyncio.run(main())
