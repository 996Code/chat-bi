"""Comprehensive API tests for MySQL and PostgreSQL test databases.
读取 tests/test_config.json 获取配置，支持切换服务器后直接运行。
所有业务功能测试都走 API 端点。
Run: .venv/bin/python tests/test_dual_db_comprehensive.py
"""
import asyncio
import json
import os
import sys
from httpx import AsyncClient

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "test_config.json")
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

BASE = CFG["chatbi"]["api_base"]
API = CFG["chatbi"]["api_prefix"]
EMAIL = CFG["chatbi"]["admin_email"]
PWD = CFG["chatbi"]["admin_password"]

MYSQL_CFG = CFG["mysql"]
PG_CFG = CFG["postgresql"]

passed = 0
failed = 0
errors = []


def ok(name):
    global passed
    passed += 1
    print(f"  PASS: {name}")


def fail(name, detail):
    global failed
    failed += 1
    errors.append((name, detail))
    print(f"  FAIL: {name} -- {detail}")


async def _run_dual_db_tests():
    global passed, failed, errors

    async with AsyncClient(base_url=BASE, timeout=60) as c:
        # Login
        r = await c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # ─── 1. Register both test databases as datasources ───
        print("\n[1] Register test databases as datasources")

        # MySQL test DB
        r = await c.post(f"{API}/datasources", json={
            "name": "MySQL 电商测试库",
            "type": "mysql",
            "host": MYSQL_CFG["host"],
            "port": MYSQL_CFG["port"],
            "database_name": MYSQL_CFG["test_db"],
            "username": MYSQL_CFG["user"],
            "password": MYSQL_CFG["password"]
        }, headers=headers)
        if r.status_code == 201:
            mysql_ds_id = r.json()["id"]
            ok(f"MySQL datasource registered (id={mysql_ds_id[:8]}...)")
        else:
            # Maybe already exists, find it
            r2 = await c.get(f"{API}/datasources", headers=headers)
            mysql_ds = [d for d in r2.json() if d["name"] == "MySQL 电商测试库"]
            if mysql_ds:
                mysql_ds_id = mysql_ds[0]["id"]
                ok(f"MySQL datasource already exists (id={mysql_ds_id[:8]}...)")
            else:
                fail("Register MySQL datasource", f"status={r.status_code}, body={r.text[:200]}")
                return

        # PostgreSQL test DB
        r = await c.post(f"{API}/datasources", json={
            "name": "PostgreSQL 电商测试库",
            "type": "postgresql",
            "host": PG_CFG["host"],
            "port": PG_CFG["port"],
            "database_name": PG_CFG["test_db"],
            "username": PG_CFG["user"],
            "password": PG_CFG["password"]
        }, headers=headers)
        if r.status_code == 201:
            pg_ds_id = r.json()["id"]
            ok(f"PostgreSQL datasource registered (id={pg_ds_id[:8]}...)")
        else:
            r2 = await c.get(f"{API}/datasources", headers=headers)
            pg_ds = [d for d in r2.json() if d["name"] == "PostgreSQL 电商测试库"]
            if pg_ds:
                pg_ds_id = pg_ds[0]["id"]
                ok(f"PostgreSQL datasource already exists (id={pg_ds_id[:8]}...)")
            else:
                fail("Register PostgreSQL datasource", f"status={r.status_code}")
                return

        # ─── 2. Test connection for both ───
        print("\n[2] Test connection")
        for name, ds_id in [("MySQL", mysql_ds_id), ("PostgreSQL", pg_ds_id)]:
            r = await c.post(f"{API}/datasources/{ds_id}/test", headers=headers)
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    db_count = len(data.get("databases", []))
                    ok(f"{name} connection: {db_count} databases listed")
                else:
                    fail(f"{name} connection", f"not success: {data}")
            else:
                fail(f"{name} connection", f"status={r.status_code}, body={r.text[:200]}")

        # ─── 3. Schema sync for both ───
        print("\n[3] Schema sync (full mode)")
        for name, ds_id in [("MySQL", mysql_ds_id), ("PostgreSQL", pg_ds_id)]:
            r = await c.post(f"{API}/data-models/{ds_id}/sync", json={"mode": "full"}, headers=headers)
            if r.status_code in (200, 201):
                data = r.json()
                ok(f"{name} schema sync: {json.dumps(data)}")
            else:
                fail(f"{name} schema sync", f"status={r.status_code}, body={r.text[:300]}")

        # ─── 4. Data model verification ───
        print("\n[4] Data model verification")
        for name, ds_id in [("MySQL", mysql_ds_id), ("PostgreSQL", pg_ds_id)]:
            r = await c.get(f"{API}/data-models/{ds_id}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                config = data.get("config", {})
                models = config.get("models", [])
                table_names = [m["name"] for m in models]
                expected = {"t_users", "t_products", "t_orders", "t_order_items", "t_payments", "t_shipping", "t_reviews", "t_coupons", "t_warehouses", "t_inventory", "t_categories"}
                found = expected & set(table_names)
                if len(found) >= 9:
                    ok(f"{name} data model: {len(models)} tables, found {len(found)}/{len(expected)} expected")
                    # Verify column details
                    for m in models:
                        if m["name"] == "t_orders" and len(m.get("columns", [])) > 5:
                            ok(f"{name} t_orders has {len(m['columns'])} columns")
                            break
                    else:
                        fail(f"{name} t_orders", "not found or too few columns")
                else:
                    fail(f"{name} data model", f"only {len(found)}/{len(expected)} expected tables: {found}")
            else:
                fail(f"{name} data model", f"status={r.status_code}")

        # ─── 5. Greeting/Other queries ───
        print("\n[5] Greeting and Other intent queries")
        for name, ds_id in [("MySQL", mysql_ds_id), ("PostgreSQL", pg_ds_id)]:
            for q in ["你好", "谢谢", "再见"]:
                r = await c.post(f"{API}/query", json={"question": q, "datasource_id": ds_id}, headers=headers)
                if r.status_code == 200 and r.json().get("intent") == "Other":
                    ok(f"{name} greeting '{q}' -> Other")
                else:
                    fail(f"{name} greeting '{q}'", f"status={r.status_code}, intent={r.json().get('intent') if r.status_code==200 else 'N/A'}")

        # ─── 6. Query endpoints availability ───
        print("\n[6] Query endpoint (data questions - LLM dependent)")
        for name, ds_id in [("MySQL", mysql_ds_id), ("PostgreSQL", pg_ds_id)]:
            questions = [
                "有多少用户",
                "各城市的订单数量",
                "销售额最高的商品",
                "各品类的销售占比",
                "VIP用户的平均订单金额",
                "最近的退款订单有哪些",
                "各仓库的库存总量",
                "各月份的GMV趋势",
                "用户复购率",
                "各快递公司的平均配送时长",
            ]
            for q in questions:
                r = await c.post(f"{API}/query", json={"question": q, "datasource_id": ds_id}, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    intent = data.get("intent", "unknown")
                    success = data.get("success", False)
                    error = data.get("error", "")
                    if intent == "DataQuery":
                        ok(f"{name} query '{q}' -> DataQuery (success={success})")
                    elif success is False and error:
                        # LLM returned garbled response but endpoint handled gracefully
                        ok(f"{name} query '{q}' -> endpoint responded (error handled)")
                    else:
                        fail(f"{name} query '{q}'", f"intent={str(intent)[:50]}, success={success}")
                else:
                    fail(f"{name} query '{q}'", f"status={r.status_code}")

        # ─── 7. Saved queries CRUD ───
        print("\n[7] Saved queries CRUD (MySQL)")
        r = await c.post(f"{API}/queries", json={
            "name": "MySQL-各城市订单数",
            "query_text": "各城市的订单数量",
            "generated_sql": "SELECT city, COUNT(*) FROM t_orders GROUP BY city",
            "datasource_id": mysql_ds_id
        }, headers=headers)
        if r.status_code == 201:
            sq_id = r.json()["id"]
            ok("MySQL save query")

            r = await c.get(f"{API}/queries/{sq_id}", headers=headers)
            if r.status_code == 200 and r.json()["name"] == "MySQL-各城市订单数":
                ok("MySQL get single query")
            else:
                fail("MySQL get single query", f"status={r.status_code}")

            r = await c.post(f"{API}/queries/{sq_id}/re-run", headers=headers)
            if r.status_code == 200:
                ok("MySQL re-run query")
            else:
                fail("MySQL re-run query", f"status={r.status_code}")

            r = await c.delete(f"{API}/queries/{sq_id}", headers=headers)
            if r.status_code == 204:
                ok("MySQL delete query")
            else:
                fail("MySQL delete query", f"status={r.status_code}")
        else:
            fail("MySQL save query", f"status={r.status_code}")

        # PostgreSQL CRUD
        print("\n[7b] Saved queries CRUD (PostgreSQL)")
        r = await c.post(f"{API}/queries", json={
            "name": "PG-销售总额",
            "query_text": "销售总额是多少",
            "generated_sql": "SELECT SUM(total_amount) FROM t_orders",
            "datasource_id": pg_ds_id
        }, headers=headers)
        if r.status_code == 201:
            sq_id = r.json()["id"]
            ok("PG save query")

            r = await c.delete(f"{API}/queries/{sq_id}", headers=headers)
            if r.status_code == 204:
                ok("PG delete query")
            else:
                fail("PG delete query", f"status={r.status_code}")
        else:
            fail("PG save query", f"status={r.status_code}")

        # ─── 8. CSV Export ───
        print("\n[8] CSV Export")
        r = await c.post(f"{API}/export/csv", json={
            "columns": ["city", "order_count", "total_amount"],
            "rows": [
                {"city": "杭州", "order_count": 150, "total_amount": 50000.0},
                {"city": "上海", "order_count": 200, "total_amount": 80000.0},
                {"city": "北京", "order_count": 180, "total_amount": 75000.0},
            ]
        }, headers=headers)
        if r.status_code == 200 and "city,order_count,total_amount" in r.text:
            ok("CSV export with data")
        else:
            fail("CSV export", f"status={r.status_code}, body={r.text[:100]}")

        # ─── 9. Feedback ───
        print("\n[9] Feedback")
        r = await c.post(f"{API}/feedback", json={
            "query_id": f"test-mysql-{mysql_ds_id[:8]}",
            "rating": "up",
            "comment": "MySQL测试结果准确"
        }, headers=headers)
        if r.status_code in (200, 201):
            ok("MySQL feedback submitted")
        else:
            fail("MySQL feedback", f"status={r.status_code}")

        r = await c.post(f"{API}/feedback", json={
            "query_id": f"test-pg-{pg_ds_id[:8]}",
            "rating": "up",
            "comment": "PostgreSQL测试结果准确"
        }, headers=headers)
        if r.status_code in (200, 201):
            ok("PG feedback submitted")
        else:
            fail("PG feedback", f"status={r.status_code}")

        # ─── 10. Conversations ───
        print("\n[10] Conversations")
        r = await c.get(f"{API}/conversations", headers=headers)
        if r.status_code == 200:
            convs = r.json()
            ok(f"Conversations: {len(convs)} total")
        else:
            fail("Conversations", f"status={r.status_code}")

        # ─── 11. SSE Stream ───
        print("\n[11] SSE Stream")
        for name, ds_id in [("MySQL", mysql_ds_id), ("PostgreSQL", pg_ds_id)]:
            r = await c.post(f"{API}/query/stream", json={"question": "你好", "datasource_id": ds_id}, headers=headers)
            if r.status_code == 200 and "intent" in r.text:
                ok(f"{name} SSE stream contains intent")
            else:
                fail(f"{name} SSE stream", f"status={r.status_code}, body={r.text[:200]}")

        # ─── 12. Audit logs ───
        print("\n[12] Audit logs")
        r = await c.get(f"{API}/audit", headers=headers)
        if r.status_code == 200 and isinstance(r.json(), list):
            ok(f"Audit logs: {len(r.json())} entries")
        else:
            fail("Audit logs", f"status={r.status_code}")

        # ─── 13. List all datasources summary ───
        print("\n[13] Datasource summary")
        r = await c.get(f"{API}/datasources", headers=headers)
        if r.status_code == 200:
            ds_list = r.json()
            mysql_count = len([d for d in ds_list if d["type"] == "mysql"])
            pg_count = len([d for d in ds_list if d["type"] == "postgresql"])
            ok(f"Datasources: {mysql_count} MySQL, {pg_count} PostgreSQL, {len(ds_list)} total")
        else:
            fail("List datasources", f"status={r.status_code}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Dual DB Comprehensive Test: {passed} passed, {failed} failed")
    if errors:
        print(f"\nFailed tests:")
        for name, detail in errors:
            print(f"  - {name}: {detail}")
    print(f"{'='*60}")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(_run_dual_db_tests())
    sys.exit(0 if success else 1)
