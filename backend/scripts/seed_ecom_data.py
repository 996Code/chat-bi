"""
电商演示库数据补充脚本 — 填充空表, 扩充已有数据

设计原则:
  1. 数据关联完整: FK 关系正确, 外键引用都指向已有数据
  2. 时间范围合理: 数据集中在近 6 个月 (2026-01 ~ 2026-07),
     确保 CURRENT_DATE 相关查询能查到数据
  3. 数据量适中: 核心表万级, 日志表十万级, 不撑爆但够复杂

用法:
  cd backend && uv run python scripts/seed_ecom_data.py
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

import psycopg2

# ── 连接配置 (从环境变量读取, 兼容 Docker 部署) ──────────────
DB_CONFIG = {
    "host": os.getenv("SAMPLE_DB_HOST", "localhost"),
    "port": int(os.getenv("SAMPLE_DB_PORT", "5432")),
    "database": os.getenv("SAMPLE_DB_NAME", "chatbi_ecom"),
    "user": os.getenv("SAMPLE_DB_USER", "root"),
    "password": os.getenv("SAMPLE_DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "root")),
}

# ── 常量 ──────────────────────────────────────────────────────
NOW = datetime(2026, 7, 4, 12, 0, 0)
SIX_MONTHS_AGO = NOW - timedelta(days=180)
THREE_MONTHS_AGO = NOW - timedelta(days=90)
ONE_MONTH_AGO = NOW - timedelta(days=30)

DEVICES = ["iOS", "Android", "Web", "iPad", "MiniProgram"]
LOGIN_METHODS = ["password", "sms", "wechat", "alipay", "apple"]
CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京",
    "重庆", "天津", "苏州", "长沙", "郑州", "西安", "青岛", "大连",
]
POINT_SOURCES = ["order_reward", "sign_in", "task", "promotion", "refund", "admin_adjust", "purchase"]
POINT_REMARKS = {
    "order_reward": "下单奖励积分", "sign_in": "每日签到", "task": "完成任务奖励",
    "promotion": "活动奖励", "refund": "退款返还", "admin_adjust": "系统调整",
    "purchase": "积分兑换扣减",
}
REFUND_REASONS = ["质量问题", "尺寸不符", "商品损坏", "发错货", "不喜欢", "其他原因"]
TICKET_STATUS = ["pending", "processing", "resolved", "closed"]
FUNNEL_STEPS = ["view_home", "view_category", "view_product", "add_cart", "submit_order", "pay_success"]
FUNNEL_SOURCES = ["home_banner", "search", "recommend", "category_nav", "push"]
PAGE_TITLES = ["首页", "分类页", "商品详情", "购物车", "确认订单", "订单列表", "个人中心", "搜索结果"]
APP_EVENT_NAMES = ["click", "scroll", "swipe", "submit", "share", "view", "purchase"]
PLATFORMS = ["ios", "android", "web", "miniapp"]
APP_VERSIONS = ["1.0.0", "1.1.0", "1.2.0", "2.0.0", "2.1.0"]
FEEDBACK_TYPES = ["bug", "feature", "complaint", "suggestion"]
TICKET_CATEGORIES = ["order", "refund", "product", "account", "logistics", "other"]
TICKET_SOURCES = ["web", "app", "phone", "wechat"]
AFTER_SALE_TYPES = ["refund_only", "return_refund", "exchange"]
TXN_TYPES = ["payment", "refund", "withdraw", "commission", "settlement"]
PAYMENT_METHODS = ["alipay", "wechat", "card", "balance"]
SHOP_ROLES = ["owner", "manager", "staff", "cs"]


def rand_ip() -> str:
    return f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,255)}"


def rand_date(start: datetime, end: datetime) -> datetime:
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.uniform(0, delta))


def rand_str(n: int = 8) -> str:
    return "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=n))


def batch_insert(cur, sql: str, rows: list[tuple], batch_size: int = 5000,
                 on_conflict: str | None = None) -> int:
    """批量插入, 自动分批。on_conflict 为 PostgreSQL ON CONFLICT 子句 (不含 ON CONFLICT 前缀)。"""
    if on_conflict:
        sql = f"{sql} ON CONFLICT {on_conflict}"
    total = 0
    for i in range(0, len(rows), batch_size):
        cur.executemany(sql, rows[i : i + batch_size])
        total += len(rows[i : i + batch_size])
    return total


def main() -> None:
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    # ── 幂等判断: 如果 biz_orders 已有数据则跳过 ──────────────────
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='biz_orders'")
    if cur.fetchone()[0] > 0:
        cur.execute("SELECT COUNT(*) FROM biz_orders")
        if cur.fetchone()[0] > 0:
            print("✓ 业务库已有数据, 跳过初始化 (如需重灌请先 TRUNCATE)")
            cur.close()
            conn.close()
            return

    # ── 幂等: 先清空要插入的表 (按 FK 依赖倒序) ──────────────────────
    TRUNCATE_ORDER = [
        # 子表先删
        "cs_after_sales_logs", "cs_after_sales", "cs_ticket_messages", "cs_service_tickets",
        "mkt_promotion_products",
        "biz_order_logistics", "biz_order_payments", "biz_order_items",
        "biz_order_status_logs", "biz_refund_items", "biz_order_refunds",
        "biz_user_coupons", "biz_cart_items",
        "fin_settlement_items", "fin_settlements", "fin_invoices", "fin_transactions",
        "ops_product_daily_reports", "ops_app_events", "ops_page_views", "ops_user_funnel_logs",
        "sr_user_browse_histories",
        "pd_product_favorites", "pd_sku_prices", "pd_sku_stocks",
        "uc_feedbacks", "uc_user_tags", "uc_points_logs", "uc_login_logs",
        # 最后删订单 (其他表可能 FK 引用)
        "biz_orders",
    ]
    print("清空已有种子数据 ...")
    for tbl in TRUNCATE_ORDER:
        cur.execute(f"TRUNCATE TABLE {tbl} CASCADE")
    print("   已清空")

    # ── 读取已有数据 ──────────────────────────────────────────────
    def fetch_ids(table: str) -> list[int]:
        cur.execute(f"SELECT id FROM {table} ORDER BY id")
        return [r[0] for r in cur.fetchall()]

    user_ids = fetch_ids("uc_users")
    product_ids = fetch_ids("pd_products")
    sku_ids = fetch_ids("pd_product_skus")
    category_ids = fetch_ids("pd_categories")
    shop_ids = fetch_ids("st_shops")
    coupon_ids = fetch_ids("biz_coupons")
    warehouse_ids = fetch_ids("wms_warehouses")
    supplier_ids = fetch_ids("sup_suppliers")
    promotion_ids = fetch_ids("mkt_promotions")
    logistics_ids = fetch_ids("wms_logistics_companies")
    brand_ids = fetch_ids("pd_brands")

    cur.execute("SELECT user_id, balance FROM uc_points_accounts ORDER BY id")
    points_accounts = cur.fetchall()

    print(f"已有: {len(user_ids)} 用户, {len(product_ids)} 商品, {len(sku_ids)} SKU, "
          f"{len(shop_ids)} 店铺")

    # ── 先创建订单 (其他很多表依赖 order_ids) ────────────────────────
    print("0. 创建订单 ...")
    # 2,000 基础订单 (过去 6 个月)
    order_rows = []
    for _ in range(2000):
        uid = random.choice(user_ids)
        sid = random.choice(shop_ids)
        order_date = rand_date(SIX_MONTHS_AGO, NOW)
        total = random.randint(50, 5000)
        discount = int(total * random.uniform(0, 0.15))
        freight = random.randint(0, 20)
        actual = total - discount + freight
        status = random.choice(["completed", "received", "shipped", "paid", "pending"])
        order_rows.append((f"ORD{rand_str(8)}", uid, sid, total, discount, freight, actual,
                           random.choice(coupon_ids) if random.random() < 0.3 else None,
                           status,
                           random.choice(PAYMENT_METHODS) if status != "pending" else None,
                           order_date if status != "pending" else None,
                           order_date, order_date))
    # 500 近期订单 (7 月份, 确保"本月"有数据)
    for _ in range(500):
        uid = random.choice(user_ids)
        sid = random.choice(shop_ids)
        order_date = rand_date(datetime(2026, 7, 1), NOW)
        total = random.randint(50, 5000)
        discount = int(total * random.uniform(0, 0.15))
        freight = random.randint(0, 20)
        actual = total - discount + freight
        status = random.choice(["completed", "received", "shipped", "paid"])
        order_rows.append((f"ORD{rand_str(8)}", uid, sid, total, discount, freight, actual,
                           random.choice(coupon_ids) if random.random() < 0.3 else None,
                           status, random.choice(PAYMENT_METHODS),
                           order_date, order_date, order_date))
    batch_insert(cur, "INSERT INTO biz_orders (order_no, user_id, shop_id, total_amount, discount_amount, freight_amount, actual_amount, coupon_id, status, payment_method, paid_at, created_at, updated_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", order_rows)

    order_ids = fetch_ids("biz_orders")

    # 订单项
    cur.execute("SELECT id, product_id FROM pd_product_skus")
    sku_to_product = {r[0]: r[1] for r in cur.fetchall()}
    oi_rows = []
    for oid in order_ids:
        for _ in range(random.randint(1, 3)):
            sku_id = random.choice(sku_ids)
            product_id = sku_to_product.get(sku_id, sku_id)
            price = random.randint(10, 1000)
            qty = random.randint(1, 3)
            oi_rows.append((oid, sku_id, product_id, f"商品{product_id}",
                            f"规格{sku_id}", price, qty, price * qty, 0))
    batch_insert(cur, "INSERT INTO biz_order_items (order_id, sku_id, product_id, product_name, sku_spec, price, quantity, subtotal, refund_status) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", oi_rows)

    order_item_ids = fetch_ids("biz_order_items")

    # 支付
    pay_rows = []
    for oid in order_ids:
        pay_rows.append((oid, f"PAY{rand_str(8)}", random.choice(PAYMENT_METHODS),
                         random.randint(100, 5000), 1, NOW, NOW))
    batch_insert(cur, "INSERT INTO biz_order_payments (order_id, payment_no, payment_method, amount, status, paid_at, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s)", pay_rows)

    # 物流
    log_rows = []
    lc_names = ["顺丰速运", "中通快递", "韵达快递", "圆通速递", "申通快递", "京东物流", "百世快递"]
    for oid in order_ids:
        if random.random() > 0.2:
            log_rows.append((oid, random.choice(lc_names),
                             f"SF{random.randint(1000000000, 9999999999)}",
                             random.choice(["shipped", "delivered", "signed"]),
                             NOW, random.choice([None, NOW])))
    batch_insert(cur, "INSERT INTO biz_order_logistics (order_id, logistics_company, tracking_no, status, shipped_at, received_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", log_rows)
    print(f"   {len(order_rows):,} 订单 + {len(oi_rows):,} 订单项 + "
          f"{len(pay_rows):,} 支付 + {len(log_rows):,} 物流")

    # ── 1. uc_login_logs ─────────────────────────────────────────
    print("1. uc_login_logs ...")
    rows = []
    for uid in user_ids:
        for _ in range(random.randint(10, 30)):
            rows.append((uid, rand_ip(), random.choice(DEVICES),
                         random.choice(LOGIN_METHODS), random.choice(CITIES),
                         rand_date(SIX_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO uc_login_logs (user_id, login_ip, login_device, login_method, location, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 2. uc_points_logs ────────────────────────────────────────
    print("2. uc_points_logs ...")
    rows = []
    for uid, current_balance in points_accounts:
        balance = current_balance
        for _ in range(random.randint(5, 20)):
            source = random.choice(POINT_SOURCES)
            if source == "purchase":
                change = -random.randint(10, 200)
            elif source == "refund":
                change = random.randint(10, 100)
            elif source == "admin_adjust":
                change = random.choice([-1, 1]) * random.randint(1, 50)
            else:
                change = random.randint(5, 50)
            balance += change
            rows.append((uid, change, max(0, balance), source,
                         random.randint(1, 10000) if random.random() > 0.3 else None,
                         POINT_REMARKS.get(source, ""), rand_date(SIX_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO uc_points_logs (user_id, change_amount, balance_after, source_type, source_id, remark, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 3. uc_user_tags ──────────────────────────────────────────
    print("3. uc_user_tags ...")
    TAG_NAMES = ["高消费", "低活跃", "新用户", "老用户", "价格敏感", "品质优先",
                 "促销响应", "复购用户", "退货率高", "VIP潜客", "羊毛党", "沉默用户"]
    rows = []
    for uid in user_ids:
        for tag in random.sample(TAG_NAMES, random.randint(1, 4)):
            rows.append((uid, tag, rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO uc_user_tags (user_id, tag_name, created_at) VALUES (%s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 4. uc_feedbacks ──────────────────────────────────────────
    print("4. uc_feedbacks ...")
    rows = []
    for uid in random.sample(user_ids, min(200, len(user_ids))):
        rows.append((uid, random.choice(FEEDBACK_TYPES),
                     f"反馈内容{random.randint(1,999)}",
                     random.choice(["", "contact@example.com"]),
                     random.randint(0, 2),  # status: 0=pending, 1=replied, 2=closed
                     None,  # reply
                     rand_date(ONE_MONTH_AGO, NOW)))
    batch_insert(cur, "INSERT INTO uc_feedbacks (user_id, type, content, contact, status, reply, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 5. pd_sku_stocks ─────────────────────────────────────────
    print("5. pd_sku_stocks ...")
    rows = []
    for sku_id in sku_ids:
        wh_id = random.choice(warehouse_ids)
        avail = random.randint(0, 5) if random.random() < 0.1 else random.randint(10, 500)
        rows.append((sku_id, wh_id, avail, random.randint(0, 50), NOW))
    batch_insert(cur, "INSERT INTO pd_sku_stocks (sku_id, warehouse_id, available_stock, locked_stock, updated_at) "
                      "VALUES (%s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 6. pd_sku_prices ─────────────────────────────────────────
    print("6. pd_sku_prices ...")
    rows = []
    for sku_id in sku_ids:
        price = random.randint(10, 2000)
        start = rand_date(SIX_MONTHS_AGO, THREE_MONTHS_AGO)
        rows.append((sku_id, price, start, start + timedelta(days=random.randint(30, 180)), NOW))
    batch_insert(cur, "INSERT INTO pd_sku_prices (sku_id, price, start_at, end_at, created_at) "
                      "VALUES (%s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 7. pd_product_favorites ──────────────────────────────────
    print("7. pd_product_favorites ...")
    rows = []
    for uid in random.sample(user_ids, min(300, len(user_ids))):
        for pid in random.sample(product_ids, random.randint(1, 8)):
            rows.append((uid, pid, rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO pd_product_favorites (user_id, product_id, created_at) "
                      "VALUES (%s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 8. biz_cart_items ────────────────────────────────────────
    print("8. biz_cart_items ...")
    rows = []
    for uid in random.sample(user_ids, min(250, len(user_ids))):
        for _ in range(random.randint(1, 5)):
            rows.append((uid, random.choice(sku_ids), random.randint(1, 3), True, NOW, NOW))
    batch_insert(cur, "INSERT INTO biz_cart_items (user_id, sku_id, quantity, checked, created_at, updated_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 9. biz_order_status_logs ─────────────────────────────────
    print("9. biz_order_status_logs ...")
    rows = []
    for oid in order_ids:
        status_seq = random.sample(["created", "paid", "shipped", "received", "completed"],
                                    random.randint(2, 5))
        base_t = rand_date(SIX_MONTHS_AGO, NOW)
        for i, st in enumerate(status_seq):
            prev_st = status_seq[i - 1] if i > 0 else None
            rows.append((oid, prev_st, st, "system", base_t + timedelta(hours=i * random.randint(1, 24))))
    batch_insert(cur, "INSERT INTO biz_order_status_logs (order_id, old_status, new_status, operator, created_at) "
                      "VALUES (%s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 10. biz_order_refunds + biz_refund_items ──────────────────
    print("10. biz_order_refunds + biz_refund_items ...")
    refund_rows = []
    for oid in random.sample(order_ids, min(200, len(order_ids))):
        uid = random.choice(user_ids)
        reason = random.choice(REFUND_REASONS)
        amount = random.randint(10, 500)
        refund_type = random.choice(["refund_only", "return_refund"])
        status = random.choice(["pending", "approved", "rejected", "completed"])
        t = rand_date(THREE_MONTHS_AGO, NOW)
        refund_rows.append((oid, uid, f"RF{random.randint(100000,999999)}", refund_type,
                            reason, amount, status, None, None, t))
    batch_insert(cur, "INSERT INTO biz_order_refunds "
                      "(order_id, user_id, refund_no, refund_type, reason, total_amount, status, approved_by, approved_at, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", refund_rows,
                 on_conflict="(refund_no) DO NOTHING")
    # refund items
    cur.execute("SELECT id FROM biz_order_refunds ORDER BY id DESC LIMIT %s", (len(refund_rows),))
    new_refund_ids = [r[0] for r in cur.fetchall()]
    ri_rows = []
    for rid in new_refund_ids:
        oiid = random.choice(order_item_ids)
        ri_rows.append((rid, oiid, 1, random.randint(10, 300)))
    batch_insert(cur, "INSERT INTO biz_refund_items (refund_id, order_item_id, quantity, amount) "
                      "VALUES (%s, %s, %s, %s)", ri_rows)
    print(f"   {len(refund_rows):,} 退款 + {len(ri_rows):,} 退款项")

    # ── 11. biz_user_coupons ─────────────────────────────────────
    print("11. biz_user_coupons ...")
    rows = []
    for uid in user_ids:
        for _ in range(random.randint(0, 3)):
            cid = random.choice(coupon_ids)
            used = random.random() < 0.6
            rows.append((uid, cid,
                         "used" if used else "unused",
                         rand_date(ONE_MONTH_AGO, NOW) if used else None,
                         random.choice(order_ids) if used else None,
                         rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO biz_user_coupons (user_id, coupon_id, status, used_at, order_id, received_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 12. st_shop_rates ────────────────────────────────────────
    print("12. st_shop_rates ...")
    rows = []
    for sid in shop_ids:
        for _ in range(random.randint(5, 30)):
            rows.append((sid, random.choice(user_ids), random.randint(1, 5),
                         f"评价{random.randint(1,999)}", rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO st_shop_rates (shop_id, user_id, rating, content, created_at) "
                      "VALUES (%s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 13. st_shop_staffs ───────────────────────────────────────
    print("13. st_shop_staffs ...")
    rows = []
    for sid in shop_ids:
        for uid in random.sample(user_ids, random.randint(2, 5)):
            rows.append((sid, uid, random.choice(SHOP_ROLES), rand_date(SIX_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO st_shop_staffs (shop_id, user_id, role, created_at) "
                      "VALUES (%s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 14. fin_transactions ─────────────────────────────────────
    print("14. fin_transactions ...")
    rows = []
    for _ in range(5000):
        rows.append((
            f"TXN{rand_str(12)}", random.choice(user_ids), random.choice(shop_ids),
            random.randint(100, 5000), random.choice(TXN_TYPES),
            random.choice(PAYMENT_METHODS), random.choice(["pending", "success", "failed"]),
            rand_date(SIX_MONTHS_AGO, NOW),
        ))
    batch_insert(cur, "INSERT INTO fin_transactions "
                      "(transaction_no, user_id, shop_id, amount, transaction_type, payment_method, status, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows,
                 on_conflict="(transaction_no) DO NOTHING")
    print(f"   {len(rows):,} 行")

    # ── 15. fin_settlements + fin_settlement_items ───────────────
    print("15. fin_settlements + fin_settlement_items ...")
    s_rows = []
    for sid in shop_ids:
        for m in range(6):
            month = NOW - timedelta(days=30 * m)
            period = month.strftime("%Y-%m")
            order_amt = random.randint(5000, 100000)
            commission = int(order_amt * random.uniform(0.03, 0.10))
            refund_amt = int(order_amt * random.uniform(0.01, 0.05))
            penalty = random.randint(0, 500)
            actual = order_amt - commission - refund_amt - penalty
            settled = random.random() < 0.7
            s_rows.append((sid, period, order_amt, commission, refund_amt, penalty, actual,
                           "settled" if settled else "pending",
                           month if settled else None, month))
    batch_insert(cur, "INSERT INTO fin_settlements "
                      "(shop_id, period, order_amount, commission, refund_amount, penalty, actual_amount, status, settled_at, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", s_rows)
    # settlement items
    cur.execute("SELECT id FROM fin_settlements ORDER BY id DESC LIMIT %s", (len(s_rows),))
    new_stl_ids = [r[0] for r in cur.fetchall()]
    si_rows = []
    for stl_id in new_stl_ids:
        for _ in range(random.randint(1, 5)):
            order_amt = random.randint(50, 3000)
            comm = int(order_amt * random.uniform(0.03, 0.10))
            si_rows.append((stl_id, random.choice(order_ids), order_amt, comm, random.randint(0, int(order_amt * 0.05))))
    batch_insert(cur, "INSERT INTO fin_settlement_items (settlement_id, order_id, order_amount, commission, refund_amount) "
                      "VALUES (%s, %s, %s, %s, %s)", si_rows)
    print(f"   {len(s_rows):,} 结算 + {len(si_rows):,} 结算项")

    # ── 16. fin_invoices ─────────────────────────────────────────
    print("16. fin_invoices ...")
    rows = []
    for uid in random.sample(user_ids, min(300, len(user_ids))):
        for _ in range(random.randint(1, 3)):
            rows.append((uid, random.choice(order_ids),
                         random.choice(["personal", "company"]),
                         f"抬头{random.randint(1,999)}",
                         f"税号{random.randint(100000,999999)}" if random.random() < 0.5 else None,
                         random.randint(100, 5000),
                         random.choice(["pending", "issued", "cancelled"]),
                         rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO fin_invoices (user_id, order_id, invoice_type, title, tax_no, amount, status, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 17. ops_user_funnel_logs ─────────────────────────────────
    print("17. ops_user_funnel_logs ...")
    rows = []
    for uid in random.sample(user_ids, min(400, len(user_ids))):
        for _ in range(random.randint(5, 20)):
            t = rand_date(THREE_MONTHS_AGO, NOW)
            rows.append((uid, t.date(), random.choice(FUNNEL_STEPS), random.choice(FUNNEL_SOURCES), t))
    batch_insert(cur, "INSERT INTO ops_user_funnel_logs (user_id, funnel_date, step, source, created_at) "
                      "VALUES (%s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 18. ops_page_views ───────────────────────────────────────
    print("18. ops_page_views ...")
    rows = []
    for uid in random.sample(user_ids, min(400, len(user_ids))):
        for _ in range(random.randint(5, 30)):
            title = random.choice(PAGE_TITLES)
            rows.append((uid, f"/{rand_str(6)}", title,
                         f"https://example.com/{rand_str(4)}",
                         random.randint(1, 300), rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO ops_page_views (user_id, page_url, page_title, referer, duration, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 19. ops_app_events ───────────────────────────────────────
    print("19. ops_app_events ...")
    rows = []
    for uid in random.sample(user_ids, min(350, len(user_ids))):
        for _ in range(random.randint(5, 20)):
            rows.append((uid, random.choice(APP_EVENT_NAMES),
                         f'{{"page":"p{random.randint(1,20)}"}}',
                         random.choice(PLATFORMS), random.choice(APP_VERSIONS),
                         rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO ops_app_events (user_id, event_name, event_params, platform, app_version, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 20. ops_product_daily_reports ────────────────────────────
    print("20. ops_product_daily_reports ...")
    rows = []
    dates = [(NOW - timedelta(days=i)).date() for i in range(90)]
    for pid in random.sample(product_ids, min(50, len(product_ids))):
        for d in dates:
            rows.append((pid, d, random.randint(0, 50), random.randint(0, 20),
                         random.randint(0, 15), random.randint(0, 30),
                         random.randint(100, 50000), NOW))
    batch_insert(cur, "INSERT INTO ops_product_daily_reports (product_id, report_date, views, favorites, cart_adds, orders, order_amount, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 21. sr_user_browse_histories ─────────────────────────────
    print("21. sr_user_browse_histories ...")
    rows = []
    for uid in random.sample(user_ids, min(350, len(user_ids))):
        for _ in range(random.randint(5, 25)):
            rows.append((uid, random.choice(product_ids), rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO sr_user_browse_histories (user_id, product_id, created_at) "
                      "VALUES (%s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 22. cs_service_tickets + cs_ticket_messages ──────────────
    print("22. cs_service_tickets + cs_ticket_messages ...")
    t_rows = []
    for uid in random.sample(user_ids, min(150, len(user_ids))):
        for _ in range(random.randint(1, 3)):
            t = rand_date(THREE_MONTHS_AGO, NOW)
            t_rows.append((uid, random.choice(order_ids) if random.random() < 0.5 else None,
                           random.choice(TICKET_CATEGORIES),
                           f"工单标题{random.randint(1,9999)}",
                           random.choice(TICKET_STATUS), random.randint(1, 3),
                           random.choice(TICKET_SOURCES), None, t, t))
    batch_insert(cur, "INSERT INTO cs_service_tickets "
                      "(user_id, order_id, category, title, status, priority, source, assigned_to, created_at, updated_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", t_rows)
    # ticket messages
    cur.execute("SELECT id FROM cs_service_tickets ORDER BY id DESC LIMIT %s", (len(t_rows),))
    ticket_ids = [r[0] for r in cur.fetchall()]
    m_rows = []
    for tid in ticket_ids:
        for _ in range(random.randint(1, 4)):
            m_rows.append((tid, random.choice(user_ids),
                           random.choice(["user", "agent", "system"]),
                           f"消息内容{random.randint(1,999)}", rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO cs_ticket_messages (ticket_id, sender_id, sender_type, content, created_at) "
                      "VALUES (%s, %s, %s, %s, %s)", m_rows)
    print(f"   {len(t_rows):,} 工单 + {len(m_rows):,} 消息")

    # ── 23. cs_after_sales + cs_after_sales_logs ─────────────────
    print("23. cs_after_sales + cs_after_sales_logs ...")
    as_rows = []
    for oid in random.sample(order_ids, min(100, len(order_ids))):
        as_rows.append((oid, random.choice(user_ids), random.choice(AFTER_SALE_TYPES),
                        random.choice(REFUND_REASONS), random.choice(["pending", "approved", "rejected", "completed"]),
                        random.choice(user_ids) if random.random() < 0.5 else None,
                        rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO cs_after_sales (order_id, user_id, type, reason, status, handler_id, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s)", as_rows)
    # after sales logs
    cur.execute("SELECT id FROM cs_after_sales ORDER BY id DESC LIMIT %s", (len(as_rows),))
    as_ids = [r[0] for r in cur.fetchall()]
    asl_rows = []
    for aid in as_ids:
        for st in random.sample(["created", "processing", "completed"], random.randint(1, 3)):
            asl_rows.append((aid, random.choice(user_ids), st, f"操作备注{random.randint(1,999)}", rand_date(THREE_MONTHS_AGO, NOW)))
    batch_insert(cur, "INSERT INTO cs_after_sales_logs (after_sale_id, operator_id, action, remark, created_at) "
                      "VALUES (%s, %s, %s, %s, %s)", asl_rows)
    print(f"   {len(as_rows):,} 售后 + {len(asl_rows):,} 日志")

    # ── 24. mkt_promotion_products ───────────────────────────────
    print("24. mkt_promotion_products ...")
    rows = []
    for pid in promotion_ids:
        for prod_id in random.sample(product_ids, random.randint(3, 10)):
            rows.append((pid, prod_id, random.randint(10, 500), random.randint(0, 10)))
    batch_insert(cur, "INSERT INTO mkt_promotion_products (promotion_id, product_id, promotion_price, sort_order) "
                      "VALUES (%s, %s, %s, %s)", rows)
    print(f"   {len(rows):,} 行")

    # ── 提交 ──────────────────────────────────────────────────────
    conn.commit()
    print("\n✅ 数据补充完成!")

    # 统计
    tables_to_check = [
        'uc_login_logs', 'uc_points_logs', 'uc_user_tags', 'uc_feedbacks',
        'pd_sku_stocks', 'pd_sku_prices', 'pd_product_favorites',
        'biz_cart_items', 'biz_order_status_logs', 'biz_order_refunds', 'biz_refund_items',
        'biz_user_coupons', 'st_shop_rates', 'st_shop_staffs',
        'fin_transactions', 'fin_settlements', 'fin_settlement_items', 'fin_invoices',
        'ops_user_funnel_logs', 'ops_page_views', 'ops_app_events', 'ops_product_daily_reports',
        'sr_user_browse_histories', 'cs_service_tickets', 'cs_ticket_messages',
        'cs_after_sales', 'cs_after_sales_logs', 'mkt_promotion_products',
        'biz_orders', 'biz_order_items', 'biz_order_payments', 'biz_order_logistics',
    ]
    print("\n数据统计:")
    for tbl in tables_to_check:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur.fetchone()[0]
        if cnt > 0:
            print(f"  {tbl:40s} {cnt:>8,}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    random.seed(42)
    main()
