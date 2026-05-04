"""Quick functional test of the fixes. Run: .venv/bin/python tests/test_quick.py"""
import asyncio
import json
import time
from httpx import AsyncClient

BASE = "http://127.0.0.1:8999"
API = "/chat-bi/api/v1"
EMAIL = "admin@chatbi.com"
PWD = "Test1234!"
DS = "461bddad-ecbe-4756-aaa7-02446b841a5d"

QUERIES = [
    "有多少用户",
    "各城市的订单数量",
    "各品类的销售总额",
    "各快递公司的平均配送时长",
    "销售额排名前十的商品",
    "各仓库的库存商品总价值",
    "不同支付方式的销售占比",
    "VIP用户的平均订单金额",
    "库存不足的商品有哪些",
    "优惠券的使用情况",
    "复购率是多少",
    "最近3个月的订单趋势",
    "没有下过单的用户有哪些",
    "各月份的销售额对比",
    "退款订单的支付方式分布",
]


async def main():
    async with AsyncClient(base_url=BASE, timeout=60) as c:
        # Login
        r = await c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        passed = 0
        failed = 0

        for i, q in enumerate(QUERIES, 1):
            start = time.monotonic()
            try:
                # Small delay to avoid rate limiting
                if i > 1:
                    await asyncio.sleep(2.5)
                r = await c.post(f"{API}/query", json={
                    "question": q, "datasource_id": DS
                }, headers=headers)
                data = r.json()
                elapsed = int((time.monotonic() - start) * 1000)
                success = data.get("success", False)
                sql = (data.get("sql") or "")[:100].replace("\n", " ")
                rows = data.get("row_count", "?")
                error = (data.get("error") or "none")[:60]

                has_chinese_sql = any("一" <= c <= "鿿" for c in (data.get("sql") or ""))
                if success and not has_chinese_sql:
                    status_icon = "✅"
                    passed += 1
                elif success:
                    status_icon = "⚠️(中文SQL)"
                    passed += 1
                else:
                    status_icon = "❌"
                    failed += 1

                print(f"{i:2d}. {status_icon} [{elapsed:4d}ms] {q}")
                if success:
                    print(f"    SQL: {sql}  rows={rows}")
                else:
                    print(f"    error: {error}")
            except Exception as e:
                failed += 1
                elapsed = int((time.monotonic() - start) * 1000)
                print(f"{i:2d}. ❌ [{elapsed:4d}ms] {q} — {e}")

        print(f"\n{'='*50}")
        print(f"Results: {passed} passed, {failed} failed out of {len(QUERIES)}")
        print(f"{'='*50}")
        return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    import sys
    sys.exit(0 if ok else 1)
