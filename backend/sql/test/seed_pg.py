"""Seed script: populate PostgreSQL test database with high-volume e-commerce data.

Generates ~25,000 rows across 11 tables with realistic edge cases
for complex SQL testing (JOINs, window functions, subqueries, etc.).

Table names use t_ prefix as expected by the ChatBI application.

Uses parameterized queries exclusively (no string SQL generation for data).

Usage:
    python sql/test/seed_pg.py
"""
import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timedelta, date

import asyncpg

random.seed(42)

# ─── Configuration ───────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "test_config.json")

with open(CONFIG_PATH) as f:
    _CONFIG = json.load(f)["postgresql"]

# ─── Constants ───────────────────────────────────────────────────────────────
CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "武汉", "西安", "南京",
    "苏州", "天津", "郑州", "长沙", "东莞", "青岛", "宁波", "合肥", "佛山", "福州",
    "厦门", "济南", "沈阳", "哈尔滨", "大连", "昆明", "贵阳", "南宁", "南昌", "长春",
]

CARRIER_NAMES = ["顺丰速运", "中通快递", "圆通速递", "申通快递", "韵达快递", "极兔速递", "京东物流", "邮政EMS"]

FIRST_NAMES = [
    "伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "洋", "勇",
    "杰", "军", "明", "涛", "超", "霞", "平", "刚", "桂英", "秀英",
    "建国", "志强", "文博", "天佑", "思远", "雨婷", "欣怡", "浩然", "子轩", "梓涵",
]

LAST_NAMES = [
    "王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
    "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧",
]

PRODUCT_ADJECTIVES = ["高品质", "经济实惠", "奢华", "专业级", "入门级", "旗舰", "经典"]
PRODUCT_SUFFIXES = ["标准版", "升级版", "尊享版", "青春版", "Pro版", "Lite版", "Max版"]

REVIEW_CONTENTS = [
    "质量很好，非常满意！",
    "物流很快，包装完好",
    "性价比不错，值得购买",
    "一般般吧，没有想象中好",
    "太差了，退货",
    "第二次购买了，一如既往的好",
    "颜色和图片有差距",
    "尺寸合适，做工精细",
    "客服态度好，解决问题快",
    "味道有点大，其他还可以",
    "超出预期，推荐购买",
    "价格便宜，质量也还行",
    "不推荐，和描述不符",
    "非常好的购物体验",
    "快递太慢了",
]

NOW = datetime.utcnow()
DATE_START = NOW - timedelta(days=180)

# ─── Target volumes ──────────────────────────────────────────────────────────
N_USERS = 2000
N_CATEGORIES = 40
N_PRODUCTS = 500
N_ORDERS = 5000
N_ORDER_ITEMS = 12000
N_COUPONS = 50
N_WAREHOUSES = 10


async def recreate_database():
    """Drop and recreate chatbi_test database, then create tables."""
    admin_conn = await asyncpg.connect(
        host=_CONFIG["host"],
        port=_CONFIG["port"],
        user=_CONFIG["user"],
        password=_CONFIG["password"],
        database=_CONFIG["admin_db"],
    )
    try:
        # Terminate existing connections
        await admin_conn.execute("""
            SELECT pg_terminate_backend(pid) FROM pg_stat_activity
            WHERE datname = 'chatbi_test' AND pid <> pg_backend_pid()
        """)
        await admin_conn.execute("DROP DATABASE IF EXISTS chatbi_test")
        await admin_conn.execute("CREATE DATABASE chatbi_test")
    finally:
        await admin_conn.close()

    # Create tables in the new database
    conn = await asyncpg.connect(
        host=_CONFIG["host"],
        port=_CONFIG["port"],
        user=_CONFIG["user"],
        password=_CONFIG["password"],
        database="chatbi_test",
    )
    try:
        # Read schema and execute
        schema_path = os.path.join(os.path.dirname(__file__), "01_pg_schema.sql")
        with open(schema_path) as f:
            schema_sql = f.read()

        # Extract just the CREATE TABLE / ALTER / CREATE INDEX statements
        lines = []
        skip_block = False
        for line in schema_sql.split("\n"):
            stripped = line.strip()
            if stripped.startswith("--") or not stripped:
                continue
            if stripped.startswith("SELECT ") or stripped.startswith("DROP DATABASE") or stripped.startswith("CREATE DATABASE"):
                skip_block = True
                continue
            if stripped.startswith("\\c"):
                continue
            # Skip continuation lines of skipped statements (FROM/WHERE from pg_terminate_backend)
            if skip_block and (stripped.startswith("FROM ") or stripped.startswith("WHERE ")):
                continue
            skip_block = False
            lines.append(line)

        full_sql = "\n".join(lines)
        # Split on semicolons and execute individually
        for stmt in full_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(stmt)
        return conn
    except Exception:
        await conn.close()
        raise


async def seed_categories(conn):
    """40 categories with 3-level hierarchy."""
    rows = []
    cat_id = 1

    level1_names = [
        "数码电子", "服装鞋帽", "食品饮料", "家居家装", "美妆个护",
        "母婴用品", "运动户外", "图书文具", "汽车用品", "医药保健",
    ]
    level1_ids = []
    for name in level1_names:
        rows.append((cat_id, name, None, 1, len(rows)))
        level1_ids.append(cat_id)
        cat_id += 1

    level2_map = {
        "数码电子": ["手机", "电脑", "平板", "智能穿戴"],
        "服装鞋帽": ["男装", "女装", "童装", "鞋靴"],
        "食品饮料": ["零食", "饮料", "生鲜", "酒类"],
        "家居家装": ["家具", "灯具", "家纺", "厨具"],
        "美妆个护": ["护肤", "彩妆", "洗发", "香水"],
        "母婴用品": ["奶粉", "纸尿裤", "玩具", "童车"],
        "运动户外": ["跑步", "健身", "登山", "骑行"],
        "图书文具": ["小说", "教材", "办公", "画材"],
        "汽车用品": ["车载电器", "装饰", "保养", "安全"],
        "医药保健": ["维生素", "器械", "中药", "保健品"],
    }
    level2_ids = []
    for parent_name, subs in level2_map.items():
        parent_id = level1_ids[level1_names.index(parent_name)]
        for sub in subs:
            rows.append((cat_id, sub, parent_id, 2, len(rows)))
            level2_ids.append(cat_id)
            cat_id += 1

    level3_map = {
        "手机": ["智能手机", "老人机"],
        "电脑": ["笔记本", "台式机"],
        "男装": ["衬衫", "夹克"],
        "女装": ["连衣裙", "半身裙"],
        "零食": ["坚果", "膨化食品"],
        "护肤": ["面霜", "精华液"],
        "跑步": ["跑鞋", "跑步机"],
        "小说": ["科幻小说", "言情小说"],
    }
    for parent_name, subs in level3_map.items():
        parent_id = None
        for r in rows:
            if r[1] == parent_name and r[2] is not None:
                parent_id = r[0]
                break
        if parent_id:
            for sub in subs:
                rows.append((cat_id, sub, parent_id, 3, len(rows)))
                cat_id += 1
                if cat_id > N_CATEGORIES:
                    break
        if cat_id > N_CATEGORIES:
            break

    rows = rows[:N_CATEGORIES]

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_categories (id, name, parent_id, level, sort_order) VALUES ($1, $2, $3, $4, $5)",
                *r,
            )

    # Reset sequence
    await conn.execute(f"SELECT setval('t_categories_id_seq', {max(r[0] for r in rows)})")

    return len(rows)


async def seed_users(conn):
    """2000 users with VIP distribution, city spread, edge cases."""
    rows = []
    for i in range(1, N_USERS + 1):
        username = random.choice(LAST_NAMES) + random.choice(FIRST_NAMES)
        email = f"user{i:04d}@example.com" if i <= N_USERS * 0.7 else None
        phone = f"1{''.join([str(random.randint(0, 9)) for _ in range(10)])}" if random.random() > 0.1 else None

        vip = 0
        r = random.random()
        if r < 0.5:
            vip = 0
        elif r < 0.8:
            vip = 1
        elif r < 0.93:
            vip = 2
        elif r < 0.98:
            vip = 3
        else:
            vip = 4

        city = CITIES[i % len(CITIES)]

        created_at = DATE_START + timedelta(days=random.randint(0, 180), hours=random.randint(0, 23))
        rows.append((username, email, phone, vip, city, created_at))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_users (username, email, phone, vip_level, city, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_users_id_seq', {N_USERS})")
    return list(range(1, N_USERS + 1))


async def seed_products(conn):
    """500 products across categories with price ranges, stock edge cases."""
    cats = await conn.fetch("SELECT id, name, level FROM t_categories ORDER BY id")
    leaf_cats = [c for c in cats if c["level"] >= 2]
    if not leaf_cats:
        leaf_cats = cats

    rows = []
    statuses = ["active"] * 18 + ["inactive"] + ["discontinued"]

    for i in range(1, N_PRODUCTS + 1):
        cat = random.choice(leaf_cats)
        cat_name = cat["name"]
        adj = random.choice(PRODUCT_ADJECTIVES)
        suffix = random.choice(PRODUCT_SUFFIXES)
        name = f"{adj}{cat_name}{suffix} #{i:04d}"

        r = random.random()
        if r < 0.6:
            price = round(random.uniform(19.9, 199.9), 2)
        elif r < 0.85:
            price = round(random.uniform(200, 999.9), 2)
        elif r < 0.95:
            price = round(random.uniform(1000, 4999.9), 2)
        else:
            price = round(random.uniform(5000, 29999.9), 2)

        cost_price = round(price * random.uniform(0.3, 0.7), 2) if random.random() > 0.1 else None

        sr = random.random()
        if sr < 0.05:
            stock = 0
        elif sr < 0.9:
            stock = random.randint(1, 500)
        elif sr < 0.97:
            stock = random.randint(500, 2000)
        else:
            stock = random.randint(2000, 10000)

        sku = f"SKU-{cat['id']:02d}-{i:04d}"
        weight = round(random.uniform(50, 5000), 2) if random.random() > 0.15 else None
        status = random.choice(statuses)
        created = DATE_START + timedelta(days=random.randint(0, 180))
        rows.append((name, cat["id"], price, cost_price, stock, sku, weight, status, created))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_products (name, category_id, price, cost_price, stock, sku, weight, status, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_products_id_seq', {N_PRODUCTS})")
    return list(range(1, N_PRODUCTS + 1))


async def seed_orders(conn, user_ids):
    """5000 orders with edge cases."""
    statuses = ["pending", "paid", "shipped", "completed", "cancelled", "refunded"]
    status_weights = [5, 15, 15, 40, 10, 15]

    base_orders = []
    for i in range(N_ORDERS):
        city = CITIES[i % len(CITIES)]
        days_offset = (i * 180) // N_ORDERS
        created_at = DATE_START + timedelta(days=days_offset, hours=random.randint(0, 23), minutes=random.randint(0, 59))

        user_r = random.random()
        if user_r < 0.1:
            user_id = random.randint(1, 200)
        elif user_r < 0.15:
            continue  # users 1901-2000 get no orders
        else:
            user_id = random.choice(user_ids[200:1900]) if len(user_ids) > 200 else random.choice(user_ids)

        amt_r = random.random()
        if amt_r < 0.7:
            total = round(random.uniform(29.9, 499.9), 2)
        elif amt_r < 0.9:
            total = round(random.uniform(500, 2999.9), 2)
        elif amt_r < 0.98:
            total = round(random.uniform(3000, 19999.9), 2)
        else:
            total = round(random.uniform(20000, 89999.9), 2)

        disc_r = random.random()
        if disc_r < 0.20:
            discount = 0
        elif disc_r < 0.50:
            discount = round(random.uniform(5, 50), 2)
        elif disc_r < 0.80:
            discount = round(random.uniform(50, 200), 2)
        else:
            discount = round(random.uniform(200, total * 0.3), 2)

        base_orders.append((user_id, total, discount, city, created_at))

    # City coverage: ensure 100+ orders per city
    city_counts = {}
    for o in base_orders:
        city_counts[o[3]] = city_counts.get(o[3], 0) + 1
    for city, count in city_counts.items():
        if count < 100:
            for _ in range(100 - count):
                user_id = random.randint(1, 200)
                total = round(random.uniform(50, 2000), 2)
                discount = 0
                days_offset = random.randint(0, 179)
                created_at = DATE_START + timedelta(days=days_offset, hours=random.randint(0, 23))
                base_orders.append((user_id, total, discount, city, created_at))

    # Heavy buyers: 20 users with 50+ orders
    heavy_buyer_ids = list(range(1, 21))
    heavy_counts = {}
    for o in base_orders:
        if o[0] in heavy_buyer_ids:
            heavy_counts[o[0]] = heavy_counts.get(o[0], 0) + 1
    for uid in heavy_buyer_ids:
        current = heavy_counts.get(uid, 0)
        if current < 50:
            for _ in range(50 - current):
                total = round(random.uniform(50, 5000), 2)
                discount = 0
                city = random.choice(CITIES[:10])
                days_offset = random.randint(0, 179)
                created_at = DATE_START + timedelta(days=days_offset, hours=random.randint(0, 23))
                base_orders.append((uid, total, discount, city, created_at))

    # High-value orders: at least 50 with total > 50000
    hv_count = sum(1 for o in base_orders if o[1] > 50000)
    for _ in range(max(0, 50 - hv_count)):
        user_id = random.choice(heavy_buyer_ids)
        total = round(random.uniform(50001, 99999), 2)
        discount = round(random.uniform(1000, total * 0.15), 2)
        city = random.choice(CITIES[:10])
        days_offset = random.randint(0, 179)
        created_at = DATE_START + timedelta(days=days_offset, hours=random.randint(0, 23))
        base_orders.append((user_id, total, discount, city, created_at))

    base_orders = base_orders[:N_ORDERS]

    rows = []
    for (user_id, total, discount, city, created_at) in base_orders:
        status = random.choices(statuses, weights=status_weights, k=1)[0]

        order_no = f"ORD{created_at.strftime('%Y%m%d')}{len(rows)+1:06d}"

        paid_at = None
        shipped_at = None
        delivered_at = None
        cancelled_at = None
        refunded_at = None

        shipping_fee = 0
        if status in ("shipped", "completed"):
            shipping_fee = round(random.choice([0, 6, 8, 10, 12, 15, 20, 25]), 2)

        note = None
        if random.random() < 0.1:
            note = random.choice([
                "请尽快发货", "周末送货", "放快递柜", "不要放门口",
                "工作日送货", "联系后再送", "尽量快点",
            ])

        if status in ("paid", "shipped", "completed", "refunded"):
            paid_at = created_at + timedelta(hours=random.randint(0, 4))
        if status in ("shipped", "completed"):
            shipped_at = paid_at + timedelta(hours=random.randint(4, 48))
        if status == "completed":
            delivered_at = shipped_at + timedelta(days=random.randint(1, 14))
        if status == "cancelled":
            cancelled_at = created_at + timedelta(hours=random.randint(1, 48))
        if status == "refunded":
            refunded_at = created_at + timedelta(days=random.randint(3, 30))

        rows.append((order_no, user_id, total, discount, shipping_fee, status, city, note,
                     created_at, paid_at, shipped_at, delivered_at, cancelled_at, refunded_at))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_orders (order_no, user_id, total_amount, discount_amount, shipping_fee, status, city, note, "
                "created_at, paid_at, shipped_at, delivered_at, cancelled_at, refunded_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_orders_id_seq', {len(rows)})")
    order_ids = list(range(1, len(rows) + 1))
    return order_ids, rows


async def seed_order_items(conn, order_ids, order_rows, product_ids):
    """12000 order items. 30%+ orders have 3+ items."""
    items_per_order = []
    target_items = N_ORDER_ITEMS
    n_orders = len(order_ids)

    for i in range(n_orders):
        r = random.random()
        if r < 0.30:
            items_per_order.append(random.randint(3, 5))
        elif r < 0.70:
            items_per_order.append(2)
        else:
            items_per_order.append(1)

    current_total = sum(items_per_order)
    while current_total < target_items:
        idx = random.randint(0, n_orders - 1)
        items_per_order[idx] += 1
        current_total += 1
    while current_total > target_items:
        idx = random.randint(0, n_orders - 1)
        if items_per_order[idx] > 1:
            items_per_order[idx] -= 1
            current_total -= 1

    rows = []
    for i, oid in enumerate(order_ids):
        count = items_per_order[i]
        chosen_products = random.sample(product_ids, min(count, len(product_ids)))
        for pid in chosen_products:
            qty = random.randint(1, 5)
            price = round(random.uniform(9.9, 999.9), 2)
            subtotal = round(qty * price, 2)
            rows.append((oid, pid, qty, price, subtotal))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_order_items (order_id, product_id, quantity, price, subtotal) VALUES ($1, $2, $3, $4, $5)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_order_items_id_seq', {len(rows)})")
    return len(rows)


async def seed_payments(conn, order_ids, order_rows):
    """Payments for all non-pending, non-cancelled orders."""
    methods = ["alipay", "wechat", "bank_card", "unionpay", "balance"]
    rows = []
    for i, oid in enumerate(order_ids):
        status = order_rows[i][5]  # order status (index shifted due to order_no)
        if status in ("pending", "cancelled"):
            continue

        total = order_rows[i][2]
        discount = order_rows[i][3]
        amount = round(total - discount, 2)
        method = random.choice(methods)
        created_at = order_rows[i][8]
        paid_at = order_rows[i][9] or created_at + timedelta(hours=1)

        rows.append((oid, amount, method, "success", paid_at))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_payments (order_id, amount, method, status, paid_at) VALUES ($1, $2, $3, $4, $5)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_payments_id_seq', {len(rows)})")
    return len(rows)


async def seed_shipping(conn, order_ids, order_rows):
    """Shipping for shipped/completed orders. Refunded orders get NO shipping."""
    rows = []
    for i, oid in enumerate(order_ids):
        status = order_rows[i][5]
        if status in ("shipped", "completed"):
            carrier = random.choice(CARRIER_NAMES)
            tracking_no = f"{random.choice(['SF', 'ZTO', 'YTO', 'STO', 'YD', 'JT', 'JD', 'EMS'])}{random.randint(1000000000, 9999999999)}"
            shipped_at = order_rows[i][10] or (order_rows[i][8] + timedelta(days=1))
            delivered_at = order_rows[i][11] if status == "completed" else None
            rows.append((oid, carrier, tracking_no, shipped_at, delivered_at))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_shipping (order_id, carrier, tracking_no, shipped_at, delivered_at) VALUES ($1, $2, $3, $4, $5)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_shipping_id_seq', {len(rows)})")
    return len(rows)


async def seed_reviews(conn, order_ids, order_rows, product_ids):
    """800 reviews. 30% products never reviewed, 10 products with 20+ reviews."""
    rows = []
    completed_orders = [(i, oid) for i, oid in enumerate(order_ids) if order_rows[i][5] == "completed"]
    random.shuffle(completed_orders)
    selected = completed_orders[:800]

    # Hot products: 10 products with 25+ reviews each
    hot_products = product_ids[:10]
    for pid in hot_products:
        for _ in range(25):
            idx, oid = random.choice(completed_orders)
            rating = random.choices([1, 2, 3, 4, 5], weights=[2, 5, 10, 35, 48], k=1)[0]
            content = random.choice(REVIEW_CONTENTS) if random.random() > 0.2 else None
            is_anonymous = True if random.random() < 0.15 else False
            created_at = order_rows[idx][8] + timedelta(days=random.randint(1, 30))
            rows.append((oid, pid, rating, content, is_anonymous, created_at))

    # Regular reviews
    for idx, oid in selected:
        pid = random.choice(product_ids)
        rating = random.choices([1, 2, 3, 4, 5], weights=[3, 5, 12, 35, 45], k=1)[0]
        content = random.choice(REVIEW_CONTENTS) if random.random() > 0.25 else None
        is_anonymous = True if random.random() < 0.15 else False
        created_at = order_rows[idx][8] + timedelta(days=random.randint(1, 30))
        rows.append((oid, pid, rating, content, is_anonymous, created_at))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_reviews (order_id, product_id, rating, content, is_anonymous, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_reviews_id_seq', {len(rows)})")
    return len(rows)


async def seed_coupons(conn):
    """50 coupons. 10 expired."""
    discount_types = ["fixed", "percent"]
    codes = [
        "NEWYEAR2025", "SPRING50", "VIP100", "SUMMER20", "DOUBLE11",
        "BLACKFRIDAY", "WELCOME10", "FIRST50", "REBUY30", "FLASH99",
        "WINTER80", "AUTUMN60", "CLEAR75", "MEMBER40", "SUPER200",
    ]
    coupon_names = [
        "新年优惠券", "春季折扣", "VIP专享", "夏日特惠", "双11狂欢",
        "黑色星期五", "新人礼", "首单优惠", "回购券", "闪购券",
        "冬季促销", "秋季特惠", "清仓券", "会员券", "超级券",
    ]
    rows = []
    for i in range(N_COUPONS):
        if i < len(codes):
            code = codes[i]
            name = coupon_names[i]
        else:
            code = f"COUPON{i+1:03d}"
            name = None

        dt = random.choice(discount_types)
        if dt == "fixed":
            discount_value = round(random.choice([5, 10, 20, 50, 100, 200, 500]), 2)
        else:
            discount_value = round(random.choice([5, 10, 15, 20, 30, 50]), 2)

        min_order = round(discount_value * random.uniform(1.5, 5), 2)
        valid_from = DATE_START + timedelta(days=random.randint(0, 60))

        max_uses = None
        if random.random() < 0.3:
            max_uses = random.choice([100, 500, 1000, 5000])

        if i < 10:
            valid_until = DATE_START + timedelta(days=random.randint(30, 90))
        elif random.random() < 0.1:
            valid_until = NOW + timedelta(days=random.randint(1, 30))
        else:
            valid_until = NOW + timedelta(days=random.randint(30, 365))

        rows.append((name, code, discount_value, dt, min_order, max_uses, 0, valid_from, valid_until, valid_from))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_coupons (name, code, discount_value, discount_type, min_order, max_uses, used_count, valid_from, valid_until, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_coupons_id_seq', {len(rows)})")
    return len(rows)


async def seed_warehouses(conn):
    """10 warehouses across major cities."""
    wh_names = [
        "华东仓", "华南仓", "华北仓", "西南仓", "华中仓",
        "东北仓", "西北仓", "东南仓", "中央仓", "保税仓",
    ]
    wh_cities = ["上海", "广州", "北京", "成都", "武汉", "沈阳", "西安", "厦门", "郑州", "上海"]
    addresses = [f"{c}市某某路{random.randint(1, 999)}号" for c in wh_cities]
    capacities = [10000, 15000, 20000, 8000, 12000, 7000, 5000, 6000, 25000, 3000]
    manager_names = ["张经理", "李经理", "王经理", "赵经理", "刘经理", "陈经理", "杨经理", "黄经理", "周经理", "吴经理"]
    manager_phones = [f"1{''.join([str(random.randint(0,9)) for _ in range(10)])}" for _ in range(N_WAREHOUSES)]

    rows = []
    for i in range(N_WAREHOUSES):
        rows.append((wh_names[i], wh_cities[i], addresses[i], capacities[i], manager_names[i], manager_phones[i], True, DATE_START + timedelta(days=random.randint(0, 90))))

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_warehouses (name, city, address, capacity, manager_name, manager_phone, is_active, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_warehouses_id_seq', {len(rows)})")
    return list(range(1, N_WAREHOUSES + 1))


async def seed_inventory(conn, product_ids, warehouse_ids):
    """2000 inventory records. Edge cases: zero quantity, overbooked."""
    rows = []
    inv_id = 1

    for pid in product_ids:
        n_wh = random.randint(2, 5)
        chosen_wh = random.sample(warehouse_ids, min(n_wh, len(warehouse_ids)))
        for wh in chosen_wh:
            edge = random.random()
            if edge < 0.03:
                qty = 0
                reserved = 0
            elif edge < 0.05:
                qty = random.randint(1, 10)
                reserved = qty + random.randint(1, 5)
            else:
                qty = random.randint(10, 500)
                reserved = random.randint(0, int(qty * 0.3))

            updated_at = NOW - timedelta(days=random.randint(0, 30))
            rows.append((pid, wh, qty, reserved, updated_at))
            inv_id += 1
            if inv_id > 2000:
                break
        if inv_id > 2000:
            break

    rows = rows[:2000]

    async with conn.transaction():
        for r in rows:
            await conn.execute(
                "INSERT INTO t_inventory (product_id, warehouse_id, quantity, reserved, updated_at) VALUES ($1, $2, $3, $4, $5)",
                *r,
            )

    await conn.execute(f"SELECT setval('t_inventory_id_seq', {len(rows)})")
    return len(rows)


async def main():
    print("Recreating PostgreSQL database...")
    conn = await recreate_database()

    try:
        print("Seeding categories...")
        n_cats = await seed_categories(conn)
        print(f"  Categories: {n_cats}")

        print("Seeding users...")
        user_ids = await seed_users(conn)
        print(f"  Users: {len(user_ids)}")

        print("Seeding products...")
        product_ids = await seed_products(conn)
        print(f"  Products: {len(product_ids)}")

        print("Seeding orders...")
        order_ids, order_rows = await seed_orders(conn, user_ids)
        print(f"  Orders: {len(order_ids)}")

        print("Seeding order items...")
        n_items = await seed_order_items(conn, order_ids, order_rows, product_ids)
        print(f"  Order Items: {n_items}")

        print("Seeding payments...")
        n_payments = await seed_payments(conn, order_ids, order_rows)
        print(f"  Payments: {n_payments}")

        print("Seeding shipping...")
        n_shipping = await seed_shipping(conn, order_ids, order_rows)
        print(f"  Shipping: {n_shipping}")

        print("Seeding reviews...")
        n_reviews = await seed_reviews(conn, order_ids, order_rows, product_ids)
        print(f"  Reviews: {n_reviews}")

        print("Seeding coupons...")
        n_coupons = await seed_coupons(conn)
        print(f"  Coupons: {n_coupons}")

        print("Seeding warehouses...")
        warehouse_ids = await seed_warehouses(conn)
        print(f"  Warehouses: {len(warehouse_ids)}")

        print("Seeding inventory...")
        n_inventory = await seed_inventory(conn, product_ids, warehouse_ids)
        print(f"  Inventory: {n_inventory}")

        # ─── Verification ────────────────────────────────────────────────
        print("\n=== Row Counts Verification ===")
        tables = [
            "t_categories", "t_users", "t_products", "t_orders",
            "t_order_items", "t_payments", "t_shipping", "t_reviews",
            "t_coupons", "t_warehouses", "t_inventory",
        ]
        for tbl in tables:
            row = await conn.fetchrow(f"SELECT COUNT(*) FROM {tbl}")
            print(f"  {tbl:20s}: {row[0]}")

        # ─── Edge Case Verification ──────────────────────────────────────
        print("\n=== Edge Case Verification ===")

        # Users with no orders
        row = await conn.fetchrow("SELECT COUNT(*) FROM t_users WHERE id NOT IN (SELECT DISTINCT user_id FROM t_orders)")
        print(f"  Users with NO orders: {row[0]}")

        # Users with 50+ orders
        row = await conn.fetchrow("SELECT COUNT(*) FROM (SELECT user_id, COUNT(*) as cnt FROM t_orders GROUP BY user_id HAVING cnt >= 50) t")
        print(f"  Users with 50+ orders: {row[0]}")

        # Orders per city (lowest 5)
        rows = await conn.fetch("SELECT city, COUNT(*) as cnt FROM t_orders GROUP BY city ORDER BY cnt LIMIT 5")
        print(f"  Lowest 5 city order counts:")
        for r in rows:
            print(f"    {r['city']}: {r['cnt']}")

        # Refunded orders without shipping
        row = await conn.fetchrow(
            "SELECT COUNT(*) FROM t_orders o LEFT JOIN t_shipping s ON o.id = s.order_id "
            "WHERE o.status = 'refunded' AND s.id IS NULL"
        )
        print(f"  Refunded orders without shipping: {row[0]}")

        # High-value orders
        row = await conn.fetchrow("SELECT COUNT(*) FROM t_orders WHERE total_amount > 50000")
        print(f"  Orders with total_amount > 50000: {row[0]}")

        # Zero-discount orders
        row = await conn.fetchrow("SELECT COUNT(*) FROM t_orders WHERE discount_amount = 0")
        print(f"  Zero-discount orders: {row[0]}")

        # Products with no reviews
        row = await conn.fetchrow("SELECT COUNT(*) FROM t_products WHERE id NOT IN (SELECT DISTINCT product_id FROM t_reviews)")
        print(f"  Products with NO reviews: {row[0]}")

        # Category hierarchy
        rows = await conn.fetch("SELECT level, COUNT(*) FROM t_categories GROUP BY level ORDER BY level")
        print(f"  Category hierarchy:")
        for r in rows:
            print(f"    Level {r['level']}: {r[1]}")

        # Overbooked inventory
        row = await conn.fetchrow("SELECT COUNT(*) FROM t_inventory WHERE reserved > quantity")
        print(f"  Overbooked inventory (reserved > quantity): {row[0]}")

        # Zero-quantity inventory
        row = await conn.fetchrow("SELECT COUNT(*) FROM t_inventory WHERE quantity = 0")
        print(f"  Zero-quantity inventory: {row[0]}")

        print("\nPostgreSQL seed complete!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
