"""
ChatBI v2 — 示例业务数据种子脚本

为 T013 (数据源扫描) / T018 (复合指标) 准备真实数据。
库: njmind (用 psycopg2 同步驱动，autocommit 每条语句独立提交)

用法:
  python backend/scripts/seed_business_data.py

幂等: 可重复运行 (TRUNCATE + RESTART IDENTITY CASCADE)
对标: 海泰分析电商场景 (orders/users/products + GMV/客单价)
"""
import random
from datetime import datetime, timedelta

import psycopg2

CONN = dict(host="192.168.99.22", port=5432, dbname="njmind", user="njmind", password="njmind")


def main() -> None:
    random.seed(42)  # 可复现
    conn = psycopg2.connect(**CONN)
    conn.autocommit = True
    cur = conn.cursor()

    # 清空 (幂等)
    for t in ["biz_order_items", "biz_orders", "biz_products", "biz_categories", "biz_users"]:
        cur.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")

    # 分类
    categories = [("数码", None), ("服装", None), ("食品", None),
                  ("手机", 1), ("笔记本", 1), ("男装", 2), ("女装", 2)]
    for name, parent in categories:
        cur.execute("INSERT INTO biz_categories (name, parent_id, sort_order) VALUES (%s,%s,%s)",
                    (name, parent, random.randint(1, 10)))

    # 用户 (50)
    cities = ["北京", "上海", "广州", "深圳", "杭州", "成都"]
    for i in range(50):
        cur.execute(
            "INSERT INTO biz_users (username,email,phone,city,vip_level,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (f"user_{i:03d}", f"user{i}@example.com", f"138{random.randint(10000000,99999999)}",
             random.choice(cities), random.randint(0, 5),
             datetime.now() - timedelta(days=random.randint(1, 365)))
        )

    # 商品 (30)
    cur.execute("SELECT id FROM biz_categories")
    cat_ids = [r[0] for r in cur.fetchall()]
    for i in range(30):
        cur.execute(
            "INSERT INTO biz_products (name,category_id,price,stock,is_active,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (f"商品_{i:03d}", random.choice(cat_ids), round(random.uniform(9.9, 999.9), 2),
             random.randint(0, 1000), random.random() > 0.2,
             datetime.now() - timedelta(days=random.randint(1, 200)))
        )

    # 订单 (200) + 明细
    statuses = ["pending", "paid", "shipped", "cancelled"]
    payments = ["alipay", "wechat", "card", None]
    cur.execute("SELECT id, price FROM biz_products")
    product_prices = cur.fetchall()
    for i in range(200):
        uid = random.randint(1, 50)
        status = random.choices(statuses, weights=[2, 5, 4, 1])[0]
        created = datetime.now() - timedelta(days=random.randint(0, 90))
        paid = created + timedelta(hours=random.randint(1, 48)) if status in ("paid", "shipped") else None
        shipped = paid + timedelta(days=random.randint(1, 5)) if status == "shipped" else None
        cur.execute(
            """INSERT INTO biz_orders (user_id,order_no,total_amount,status,payment_method,created_at,paid_at,shipped_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (uid, f"ORD{20260000+i}", round(random.uniform(50, 3000), 2),
             status, random.choice(payments), created, paid, shipped)
        )
        order_id = cur.fetchone()[0]
        for pid, price in random.sample(product_prices, random.randint(2, 5)):
            cur.execute(
                "INSERT INTO biz_order_items (order_id,product_id,quantity,unit_price) VALUES (%s,%s,%s,%s)",
                (order_id, pid, random.randint(1, 3), price)
            )

    # 验证
    for t in ["biz_users", "biz_categories", "biz_products", "biz_orders", "biz_order_items"]:
        cur.execute(f"SELECT count(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]}")
    print("✓ 样本数据灌入完成")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
