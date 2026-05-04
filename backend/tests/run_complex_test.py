"""Run 15 complex queries and show SQL output."""
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
    async with AsyncClient(base_url=BASE, timeout=50) as c:
        r = await c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        passed = 0
        failed = 0

        for i, q in enumerate(QUERIES, 1):
            start = time.monotonic()
            if i > 1:
                await asyncio.sleep(3)
            try:
                r = await c.post(
                    f"{API}/query",
                    json={"question": q, "datasource_id": DS},
                    headers=headers,
                )
                data = r.json()
                elapsed = int((time.monotonic() - start) * 1000)
                success = data.get("success", False)
                sql = (data.get("sql") or "")[:200].replace("\n", " ")
                rows = data.get("row_count", "?")
                error = (data.get("error") or "none")[:80]
                intent = data.get("intent", "?")

                has_chinese_sql = any("一" <= c <= "鿿" for c in (data.get("sql") or ""))
                if success and not has_chinese_sql:
                    icon = "PASS"
                    passed += 1
                elif success:
                    icon = "WARN(中文SQL)"
                    passed += 1
                else:
                    icon = "FAIL"
                    failed += 1

                print(f"{i:2d}. {icon:12s} [{elapsed:5d}ms] {q}")
                print(f"      intent={intent}")
                if success:
                    print(f"      SQL: {sql}")
                    print(f"      rows={rows}")
                else:
                    print(f"      error: {error}")

            except Exception as e:
                failed += 1
                elapsed = int((time.monotonic() - start) * 1000)
                print(f"{i:2d}. FAIL         [{elapsed:5d}ms] {q}")
                print(f"      exception: {e}")

        print(f"\n{'='*60}")
        print(f"Results: {passed} passed, {failed} failed out of {len(QUERIES)}")
        print(f"{'='*60}")
        return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    import sys
    sys.exit(0 if ok else 1)
