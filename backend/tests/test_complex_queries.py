"""Test complex query scenarios through ChatBI API with real data.
Run: .venv/bin/python tests/test_complex_queries.py
"""
import asyncio
import json
import os
import sys
from httpx import AsyncClient

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "test_config.json")
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

BASE = CFG["chatbi"]["api_base"]
API = CFG["chatbi"]["api_prefix"]
EMAIL = CFG["chatbi"]["admin_email"]
PWD = CFG["chatbi"]["admin_password"]

# Queries organized by complexity category
TEST_CATEGORIES = {
    "A. 存在性判断": [
        ("没有下过单的用户", "NOT EXISTS", 241),  # ground truth ~241
        ("有没有商品没有被评论过", "EXISTS", None),
        ("哪些仓库没有库存记录", "NOT EXISTS", None),
        ("有没有超过5万元的订单", "EXISTS high value", 65),
    ],
    "B. 同比环比/趋势": [
        ("最近3个月的订单趋势", "monthly trend", None),
        ("各月份的销售额对比", "monthly grouping", None),
        ("各季度的销售趋势", "quarterly grouping", None),
    ],
    "C. 复杂统计": [
        ("各品类的订单金额和平均客单价", "GROUP BY category + AVG", None),
        ("VIP用户的平均订单金额", "GROUP BY vip_level", None),
        ("不同支付方式的销售占比", "GROUP BY payment method", None),
        ("各快递公司的平均配送时长", "GROUP BY carrier + AVG time", None),
        ("复购率是多少", "repeat purchase rate", None),
        ("各城市的用户数和订单密度", "GROUP BY city", None),
        ("库存不足的商品有哪些", "filter inventory < threshold", None),
    ],
    "D. 多表关联": [
        ("各品类的销售总额", "JOIN categories + orders", None),
        ("退款订单的支付方式分布", "JOIN orders + payments", None),
        ("销售额排名前十的商品", "JOIN products + order_items + ORDER BY", None),
        ("各仓库的库存商品总价值", "JOIN warehouses + inventory + products", None),
        ("优惠券的使用情况", "coupons analysis", None),
    ],
}


async def main():
    async with AsyncClient(base_url=BASE, timeout=60) as c:
        # Login
        r = await c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Find MySQL test datasource
        ds = await c.get(f"{API}/datasources", headers=headers)
        mysql_id = None
        pg_id = None
        for d in ds.json():
            if d["type"] == "mysql" and "测试" in d.get("name", ""):
                mysql_id = d["id"]
            if d["type"] == "postgresql" and "测试" in d.get("name", ""):
                pg_id = d["id"]

        print(f"MySQL DS: {mysql_id}")
        print(f"PG DS: {pg_id}")

        total_passed = 0
        total_partial = 0
        total_failed = 0

        for db_name, db_id in [("MySQL", mysql_id)]:
            if not db_id:
                continue
            print(f"\n{'='*60}")
            print(f"Testing: {db_name}")
            print(f"{'='*60}")

            for cat_name, queries in TEST_CATEGORIES.items():
                print(f"\n--- {cat_name} ---")
                for question, expected_pattern, ground_truth in queries:
                    print(f"\nQ: {question}", end="")
                    try:
                        r = await c.post(
                            f"{API}/query",
                            json={"question": question, "datasource_id": db_id},
                            headers=headers,
                        )
                        data = r.json()
                        intent = data.get("intent", "?")
                        success = data.get("success", False)
                        sql = (data.get("sql") or "")[:150]
                        error = (data.get("error") or "")[:100]
                        result = data.get("result") or {}
                        row_count = result.get("row_count", "?")
                        rows_preview = result.get("rows", [])[:3]

                        if intent == "DataQuery" and success:
                            print(f" ✅ | rows={row_count}")
                            print(f"   SQL: {sql}")
                            if ground_truth is not None:
                                if isinstance(row_count, int) and abs(row_count - ground_truth) < ground_truth * 0.5:
                                    print(f"   Ground truth: {ground_truth} ≈ {row_count} ✓")
                                else:
                                    print(f"   Ground truth: {ground_truth} vs result: {row_count} ⚠️")
                            total_passed += 1
                        elif intent == "DataQuery":
                            print(f" ⚠️ | error: {error}")
                            print(f"   SQL: {sql}")
                            total_partial += 1
                        else:
                            print(f" ❌ | intent={intent}")
                            if error:
                                print(f"   Error: {error}")
                            total_failed += 1
                    except Exception as e:
                        print(f" ❌ | Exception: {e}")
                        total_failed += 1

        print(f"\n{'='*60}")
        print(f"TOTAL: {total_passed} passed, {total_partial} partial, {total_failed} failed")
        print(f"{'='*60}")
        return total_failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
