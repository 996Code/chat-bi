"""Seed PostgreSQL `chatbi_test` database properly with parameterized queries."""
import asyncio
import random
from datetime import datetime, timedelta, timezone

random.seed(99)

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
    dt = start + timedelta(seconds=random.randint(0, delta))
    # asyncpg needs timezone-naive datetimes for TIMESTAMP columns
    return dt.replace(tzinfo=None)


# Use naive datetimes for PG
NOW_PG = NOW.replace(tzinfo=None)
START_PG = (NOW - timedelta(days=180)).replace(tzinfo=None)


PG_DDL = """
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
CREATE INDEX idx_order_items_order ON t_order_items(order_id);
CREATE INDEX idx_order_items_product ON t_order_items(product_id);

CREATE TABLE IF NOT EXISTS t_payments (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    amount DECIMAL(10,2) NOT NULL,
    method VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'success',
    paid_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_payments_order ON t_payments(order_id);
CREATE INDEX idx_payments_method ON t_payments(method);

CREATE TABLE IF NOT EXISTS t_shipping (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    carrier VARCHAR(100),
    tracking_no VARCHAR(100),
    shipped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMP
);
CREATE INDEX idx_shipping_order ON t_shipping(order_id);

CREATE TABLE IF NOT EXISTS t_reviews (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES t_orders(id),
    product_id INT REFERENCES t_products(id),
    rating INT NOT NULL,
    content VARCHAR(1000),
    is_anonymous BOOLEAN DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_reviews_order ON t_reviews(order_id);
CREATE INDEX idx_reviews_rating ON t_reviews(rating);

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
CREATE INDEX idx_coupons_code ON t_coupons(code);

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
"""


async def seed_postgresql():
    import asyncpg
    print("=== Seeding PostgreSQL: chatbi_test ===")

    # Connect to admin db
    conn = await asyncpg.connect(
        host="192.168.3.110", port=5432,
        user="postgresql", password="postgresql", database="postgresql"
    )

    # Drop existing connections and database
    try:
        await conn.execute("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = 'chatbi_test'
            AND pid <> pg_backend_pid()
        """)
    except Exception:
        pass
    await conn.execute("DROP DATABASE IF EXISTS chatbi_test")
    await conn.execute("CREATE DATABASE chatbi_test")
    await conn.close()
    print("  Database created")

    # Connect to new database
    conn = await asyncpg.connect(
        host="192.168.3.110", port=5432,
        user="postgresql", password="postgresql", database="chatbi_test"
    )

    # Create tables
    for stmt in PG_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            try:
                await conn.execute(stmt)
            except Exception as e:
                print(f"  WARN DDL: {e}")
    print("  Tables created")

    start = START_PG
    now = NOW_PG

    # Insert users (500)
    users = []
    for i in range(500):
        city = random.choice(CITIES)
        vip = random.choices([0, 1, 2, 3], weights=[50, 25, 15, 10])[0]
        created = rand_date(start, now)
        name = f"user_{i+1:04d}_{random.choice(['张','李','王','赵','刘','陈','杨','黄','周','吴'])}"
        users.append((name, f"user{i+1}@test.com", f"1{''.join([str(random.randint(0,9)) for _ in range(10)])}", city, vip, created, created))

    await conn.executemany(
        "INSERT INTO t_users (username, email, phone, city, vip_level, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        users
    )
    print(f"  Users: {len(users)}")

    # Insert categories (8 main + 24 sub = 32)
    cats = []
    cat_id = 0
    for cat_name in CATEGORIES:
        cat_id += 1
        cats.append((cat_id, cat_name, None, cat_id, True, now))
        for j, sub in enumerate(["标准款", "升级款", "旗舰款"]):
            cat_id += 1
            cats.append((cat_id, f"{cat_name}-{sub}", cat_id - 3, cat_id, True, now))

    await conn.executemany(
        "INSERT INTO t_categories (id, name, parent_id, sort_order, is_active, created_at) VALUES ($1,$2,$3,$4,$5,$6)",
        cats
    )
    # Reset sequence
    await conn.execute(f"SELECT setval('t_categories_id_seq', {cat_id})")
    print(f"  Categories: {len(cats)}")

    # Insert products (200)
    products = []
    for i in range(200):
        name = PRODUCT_NAMES[i % len(PRODUCT_NAMES)]
        cat = (i % 8) + 1
        price = round(random.uniform(9.9, 9999.0), 2)
        cost = round(price * random.uniform(0.3, 0.7), 2)
        stock = random.randint(0, 5000)
        weight = round(random.uniform(0.05, 15.0), 2)
        sku = f"SKU-{i+1:05d}"
        status = random.choices(["active", "inactive", "discontinued"], weights=[80, 15, 5])[0]
        created = rand_date(start, now)
        products.append((name, cat, price, cost, stock, weight, sku, status, created, created))

    await conn.executemany(
        "INSERT INTO t_products (name, category_id, price, cost_price, stock, weight, sku, status, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        products
    )
    # Get actual product IDs (SERIAL)
    prod_rows = await conn.fetch("SELECT id, name, price, category_id FROM t_products ORDER BY id")
    prod_map = {row["name"]: dict(row) for row in prod_rows}
    prods_list = [dict(r) for r in prod_rows]
    print(f"  Products: {len(prods_list)}")

    # Insert orders (1000)
    orders = []
    order_info = []  # for downstream inserts
    for i in range(1000):
        uid = random.randint(1, 500)
        order_no = f"ORD{now.strftime('%Y%m%d')}{i+1:06d}"
        status = random.choice(ORDER_STATUSES)
        city = random.choice(CITIES)
        total = round(random.uniform(50, 20000), 2)
        discount = round(total * random.uniform(0, 0.15), 2)
        fee = round(random.choice([0, 0, 0, 5, 8, 12, 15]), 2)
        created = rand_date(start, now)
        paid = created + timedelta(minutes=random.randint(1, 120)) if status != "pending" else None
        shipped = paid + timedelta(hours=random.randint(2, 48)) if status in ("shipped", "delivered") else None
        delivered = shipped + timedelta(days=random.randint(1, 7)) if status == "delivered" else None
        note = f"测试订单{i+1}" if random.random() < 0.1 else ""
        orders.append((uid, order_no, total, discount, fee, status, city, note, created, paid, shipped, delivered))
        order_info.append((total, status, created, paid, delivered))

    await conn.executemany(
        "INSERT INTO t_orders (user_id, order_no, total_amount, discount_amount, shipping_fee, status, city, note, created_at, paid_at, shipped_at, delivered_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
        orders
    )
    order_rows = await conn.fetch("SELECT id, total_amount, status, created_at, paid_at, delivered_at FROM t_orders ORDER BY id")
    print(f"  Orders: {len(order_rows)}")

    # Insert order items (2500)
    items = []
    for _ in range(2500):
        oid = random.choice(order_rows)["id"]
        pid = random.choice(prods_list)["id"]
        qty = random.randint(1, 10)
        price = round(float(random.choice(prods_list)["price"]) * random.uniform(0.8, 1.2), 2)
        subtotal = round(qty * price, 2)
        items.append((oid, pid, qty, price, subtotal))

    await conn.executemany(
        "INSERT INTO t_order_items (order_id, product_id, quantity, price, subtotal) VALUES ($1,$2,$3,$4,$5)",
        items
    )
    print(f"  Order items: {len(items)}")

    # Insert payments
    payments = []
    for o in order_rows:
        if o["status"] == "pending":
            continue
        method = random.choice(PAYMENT_METHODS)
        tid = f"TXN{''.join([str(random.randint(0,9)) for _ in range(12)])}"
        paid_at = o["paid_at"] or o["created_at"] + timedelta(minutes=5)
        payments.append((o["id"], float(o["total_amount"]), method, tid, "success", paid_at))

    await conn.executemany(
        "INSERT INTO t_payments (order_id, amount, method, transaction_id, status, paid_at) VALUES ($1,$2,$3,$4,$5,$6)",
        payments
    )
    print(f"  Payments: {len(payments)}")

    # Insert shipping
    shipments = []
    for o in order_rows:
        if o["status"] not in ("shipped", "delivered"):
            continue
        carrier = random.choice(SHIPPING_CARRIERS)
        tracking = f"{carrier[0]}{''.join([str(random.randint(0,9)) for _ in range(12)])}"
        shipped = (o["paid_at"] or o["created_at"]) + timedelta(hours=random.randint(2, 48))
        delivered = shipped + timedelta(days=random.randint(1, 7)) if o["status"] == "delivered" else None
        shipments.append((o["id"], carrier, tracking, shipped, delivered))

    await conn.executemany(
        "INSERT INTO t_shipping (order_id, carrier, tracking_no, shipped_at, delivered_at) VALUES ($1,$2,$3,$4,$5)",
        shipments
    )
    print(f"  Shipping: {len(shipments)}")

    # Insert reviews (for delivered orders, ~60% rate)
    reviews = []
    for o in order_rows:
        if o["status"] != "delivered" or random.random() < 0.4:
            continue
        pid = random.choice(prods_list)["id"]
        rating = random.choices([1, 2, 3, 4, 5], weights=[5, 5, 10, 30, 50])[0]
        content = random.choice(["很好", "不错", "一般", "质量可以", "推荐购买", "物流快", "包装好", "性价比很高"])
        anon = random.random() < 0.2
        created_at = o["delivered_at"] or (o["paid_at"] or o["created_at"]) + timedelta(days=5)
        reviews.append((o["id"], pid, rating, content, anon, created_at))

    await conn.executemany(
        "INSERT INTO t_reviews (order_id, product_id, rating, content, is_anonymous, created_at) VALUES ($1,$2,$3,$4,$5,$6)",
        reviews
    )
    print(f"  Reviews: {len(reviews)}")

    # Insert coupons (20)
    coupons = []
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
        is_active = random.random() < 0.7
        coupons.append((code, name, dtype, dval, min_order, max_uses, used, valid_from, valid_until, is_active))

    await conn.executemany(
        "INSERT INTO t_coupons (code, name, discount_type, discount_value, min_order, max_uses, used_count, valid_from, valid_until, is_active) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        coupons
    )
    print(f"  Coupons: {len(coupons)}")

    # Insert warehouses (5)
    warehouses = [
        ("华东仓", "上海", 50000, "上海市浦东新区XX路100号", "张经理", "13800000001"),
        ("华南仓", "广州", 40000, "广州市白云区XX路200号", "李经理", "13800000002"),
        ("华北仓", "北京", 35000, "北京市大兴区XX路300号", "王经理", "13800000003"),
        ("西南仓", "成都", 25000, "成都市双流区XX路400号", "赵经理", "13800000004"),
        ("华中仓", "武汉", 30000, "武汉市江夏区XX路500号", "刘经理", "13800000005"),
    ]
    for i, (name, city, cap, addr, mgr, phone) in enumerate(warehouses):
        await conn.execute(
            "INSERT INTO t_warehouses (id, name, city, capacity, address, manager_name, manager_phone, is_active, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            i+1, name, city, cap, addr, mgr, phone, True, now
        )
    await conn.execute("SELECT setval('t_warehouses_id_seq', 5)")
    print(f"  Warehouses: 5")

    # Insert inventory (500 unique product-warehouse combos)
    seen = set()
    inv = []
    for _ in range(500):
        pid = random.choice(prods_list)["id"]
        wid = random.randint(1, 5)
        if (pid, wid) in seen:
            continue
        seen.add((pid, wid))
        qty = random.randint(0, 2000)
        reserved = random.randint(0, min(qty, 100))
        inv.append((pid, wid, qty, reserved, now))

    await conn.executemany(
        "INSERT INTO t_inventory (product_id, warehouse_id, quantity, reserved, updated_at) VALUES ($1,$2,$3,$4,$5)",
        inv
    )
    print(f"  Inventory: {len(inv)}")

    # Verify
    print("\n  Final counts:")
    tables = ["t_users", "t_categories", "t_products", "t_orders", "t_order_items",
              "t_payments", "t_shipping", "t_reviews", "t_coupons", "t_warehouses", "t_inventory"]
    for t in tables:
        r = await conn.fetchrow(f"SELECT COUNT(*) FROM {t}")
        print(f"    {t}: {r[0]}")

    await conn.close()
    print("  PostgreSQL seed completed successfully!")


if __name__ == "__main__":
    asyncio.run(seed_postgresql())
