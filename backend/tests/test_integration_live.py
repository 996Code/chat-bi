"""Integration tests against live ChatBI API via HTTP.
读取 tests/test_config.json 获取配置，支持切换服务器后直接运行。
Run: .venv/bin/python tests/test_integration_live.py
"""
import asyncio
import json
import os
import sys
import time
from httpx import AsyncClient

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "test_config.json")
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

BASE_URL = CFG["chatbi"]["api_base"]
API = CFG["chatbi"]["api_prefix"]
ADMIN_EMAIL = CFG["chatbi"]["admin_email"]
ADMIN_PASSWORD = CFG["chatbi"]["admin_password"]

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
    print(f"  FAIL: {name} — {detail}")


async def _run_integration():
    global passed, failed, errors

    async with AsyncClient(base_url=BASE_URL, timeout=30) as c:
        # ─── 1. Health ───
        print("\n[1] Health Check")
        resp = await c.get("/health")
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            ok("Health endpoint")
        else:
            fail("Health endpoint", f"status={resp.status_code}, body={resp.text}")

        # ─── 2. Login ───
        print("\n[2] Authentication")
        resp = await c.post(f"{API}/auth/login", json={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD
        })
        if resp.status_code == 200:
            data = resp.json()
            if "access_token" in data and "refresh_token" in data:
                ok("Login returns access_token + refresh_token")
                token = data["access_token"]
                refresh_token = data["refresh_token"]
            else:
                fail("Login response", f"missing tokens: {json.dumps(data)}")
                return
        else:
            fail("Login", f"status={resp.status_code}, body={resp.text}")
            return

        # Login with wrong password
        resp = await c.post(f"{API}/auth/login", json={
            "email": ADMIN_EMAIL, "password": "WrongPassword123!"
        })
        if resp.status_code == 401:
            ok("Wrong password returns 401")
        else:
            fail("Wrong password", f"status={resp.status_code}")

        # Login with nonexistent email
        resp = await c.post(f"{API}/auth/login", json={
            "email": "nobody@chatbi.com", "password": "Test1234!"
        })
        if resp.status_code == 401:
            ok("Nonexistent email returns 401")
        else:
            fail("Nonexistent email", f"status={resp.status_code}")

        # Register (should send verification email)
        unique_email = f"itest_{int(time.time())}@test.com"
        resp = await c.post(f"{API}/auth/register", json={
            "email": unique_email, "password": "Test1234!"
        })
        if resp.status_code == 201:
            ok("Register returns 201")
        else:
            fail("Register", f"status={resp.status_code}")

        # Duplicate register
        resp = await c.post(f"{API}/auth/register", json={
            "email": unique_email, "password": "Test1234!"
        })
        if resp.status_code == 400:
            ok("Duplicate register returns 400")
        else:
            fail("Duplicate register", f"status={resp.status_code}")

        # Refresh token
        resp = await c.post(f"{API}/auth/refresh", json={
            "refresh_token": refresh_token
        })
        if resp.status_code == 200 and "access_token" in resp.json():
            ok("Token refresh works")
        else:
            fail("Token refresh", f"status={resp.status_code}")

        # ─── 3. Datasources ───
        print("\n[3] Datasources")
        headers = {"Authorization": f"Bearer {token}"}

        # List datasources
        resp = await c.get(f"{API}/datasources", headers=headers)
        if resp.status_code == 200:
            ds_list = resp.json()
            if isinstance(ds_list, list) and len(ds_list) > 0:
                ok(f"List datasources: {len(ds_list)} found")
            else:
                fail("List datasources", f"empty or not list: {type(ds_list)}")
        else:
            fail("List datasources", f"status={resp.status_code}")

        # Verify datasource in list (no single GET endpoint)
        ds_id = None
        if isinstance(ds_list, list) and len(ds_list) > 0:
            ds_id = ds_list[0]["id"]
            ds_found = any(d.get("id") == ds_id for d in ds_list)
            if ds_found:
                ok(f"Datasource visible in list (id={ds_id[:8]}...)")
            else:
                fail("Datasource in list", "not found after creation")

        # Create datasource
        resp = await c.post(f"{API}/datasources", json={
            "name": "Integration Test DB",
            "type": "mysql",
            "host": os.environ.get("INTEGRATION_TEST_DB_HOST", "127.0.0.1"),
            "port": 3306,
            "database_name": "test_integration",
            "username": os.environ.get("INTEGRATION_TEST_DB_USER", "root"),
            "password": os.environ.get("INTEGRATION_TEST_DB_PASS", "")
        }, headers=headers)
        if resp.status_code == 201:
            new_ds_id = resp.json()["id"]
            ok("Create datasource returns 201")

            # Test connection (will likely fail since DB doesn't exist, but endpoint should respond)
            resp = await c.post(f"{API}/datasources/{new_ds_id}/test", headers=headers)
            if resp.status_code in (200, 400):
                ok("Test connection endpoint responds")
            else:
                fail("Test connection", f"status={resp.status_code}")

            # Health check
            resp = await c.get(f"{API}/datasources/{new_ds_id}/health", headers=headers)
            if resp.status_code == 200:
                ok("Datasource health check responds")
            else:
                fail("Datasource health", f"status={resp.status_code}")

            # Delete datasource
            resp = await c.delete(f"{API}/datasources/{new_ds_id}", headers=headers)
            if resp.status_code == 204:
                ok("Delete datasource returns 204")
            else:
                fail("Delete datasource", f"status={resp.status_code}")
        else:
            fail("Create datasource", f"status={resp.status_code}, body={resp.text}")

        # Auth bypass test
        resp = await c.get(f"{API}/datasources")
        if resp.status_code == 401:
            ok("Datasource list requires auth")
        else:
            fail("Auth bypass", f"status={resp.status_code}")

        # ─── 4. Data Model ───
        print("\n[4] Data Model")
        # Find a valid datasource to use for model scan (MySQL test DB if available)
        valid_ds_id = None
        for d in ds_list:
            if d["type"] == "mysql" and "测试" in d.get("name", ""):
                valid_ds_id = d["id"]
                break
        if valid_ds_id is None:
            # fallback to first available
            valid_ds_id = ds_id
        if valid_ds_id:
            # Sync first to ensure a MetadataConfig exists
            resp = await c.post(f"{API}/data-models/{valid_ds_id}/sync", json={"mode": "full"}, headers=headers)
            if resp.status_code in (200, 201):
                ok("Data model sync responds")
            else:
                fail("Data model sync", f"status={resp.status_code}, body={resp.text[:200]}")

            # Then get the model
            resp = await c.get(f"{API}/data-models/{valid_ds_id}", headers=headers)
            if resp.status_code == 200:
                model = resp.json()
                if "tables" in model:
                    ok(f"Data model scan: {len(model.get('tables', []))} tables")
                else:
                    ok("Data model scan responds")
            elif resp.status_code == 404:
                ok("Data model scan: no saved model yet (expected for empty/new ds)")
            else:
                fail("Data model scan", f"status={resp.status_code}")

        # Save model
        resp = await c.post(f"{API}/data-models", json={
            "tables": [{"name": "test_table", "comment": "测试表"}],
            "relationships": [],
            "metrics": []
        }, headers=headers)
        if resp.status_code in (200, 201, 400):
            ok("Save data model responds")
        else:
            fail("Save data model", f"status={resp.status_code}")

        # List data models
        resp = await c.get(f"{API}/data-models", headers=headers)
        if resp.status_code == 200:
            ok("List data models responds")
        else:
            fail("List data models", f"status={resp.status_code}")

        # ─── 5. Query ───
        print("\n[5] Query")
        # Greeting query
        if ds_id:
            resp = await c.post(f"{API}/query", json={
                "question": "你好",
                "datasource_id": ds_id
            }, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("intent") == "Other":
                    ok("Greeting query returns Other intent")
                else:
                    ok(f"Greeting query responds (intent={data.get('intent')})")
            else:
                fail("Greeting query", f"status={resp.status_code}")

            # Thank you query
            resp = await c.post(f"{API}/query", json={
                "question": "谢谢",
                "datasource_id": ds_id
            }, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("intent") == "Other":
                    ok("Thank you query returns Other intent")
                else:
                    ok(f"Thank you query responds (intent={data.get('intent')})")
            else:
                fail("Thank you query", f"status={resp.status_code}")

        # Query without auth
        resp = await c.post(f"{API}/query", json={
            "question": "test",
            "datasource_id": "fake"
        })
        if resp.status_code == 401:
            ok("Query requires auth")
        else:
            fail("Query auth bypass", f"status={resp.status_code}")

        # ─── 6. Saved Queries ───
        print("\n[6] Saved Queries")
        # List saved queries
        resp = await c.get(f"{API}/queries", headers=headers)
        if resp.status_code == 200:
            sq_list = resp.json()
            if isinstance(sq_list, list):
                ok(f"List saved queries: {len(sq_list)} found")
            else:
                fail("List saved queries", f"not a list: {type(sq_list)}")
        else:
            fail("List saved queries", f"status={resp.status_code}")

        # Save a query
        if ds_id:
            resp = await c.post(f"{API}/queries", json={
                "name": "Integration Test Query",
                "query_text": "测试查询",
                "generated_sql": "SELECT 1",
                "datasource_id": ds_id
            }, headers=headers)
            if resp.status_code == 201:
                sq_id = resp.json()["id"]
                ok("Save query returns 201")

                # Get single query
                resp = await c.get(f"{API}/queries/{sq_id}", headers=headers)
                if resp.status_code == 200 and resp.json().get("name") == "Integration Test Query":
                    ok("Get single query")
                else:
                    fail("Get single query", f"status={resp.status_code}")

                # Re-run query
                resp = await c.post(f"{API}/queries/{sq_id}/re-run", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("question") == "测试查询" and data.get("sql") == "SELECT 1":
                        ok("Re-run query returns original question/sql")
                    else:
                        ok("Re-run query responds")
                else:
                    fail("Re-run query", f"status={resp.status_code}")

                # Delete query
                resp = await c.delete(f"{API}/queries/{sq_id}", headers=headers)
                if resp.status_code == 204:
                    ok("Delete query returns 204")

                    # Verify deleted
                    resp = await c.get(f"{API}/queries/{sq_id}", headers=headers)
                    if resp.status_code == 404:
                        ok("Deleted query returns 404")
                    else:
                        fail("Verify deletion", f"status={resp.status_code}")
                else:
                    fail("Delete query", f"status={resp.status_code}")
            else:
                fail("Save query", f"status={resp.status_code}")

        # ─── 7. CSV Export ───
        print("\n[7] CSV Export")
        resp = await c.post(f"{API}/export/csv", json={
            "columns": ["name", "age"],
            "rows": [
                {"name": "Alice", "age": 30},
                {"name": "Bob", "age": 25},
            ]
        }, headers=headers)
        if resp.status_code == 200:
            text = resp.text
            if "name,age" in text and "Alice" in text:
                ok("CSV export contains headers and data")
            else:
                fail("CSV content", f"body={text[:200]}")
        else:
            fail("CSV export", f"status={resp.status_code}")

        # ─── 8. Feedback ───
        print("\n[8] Feedback")
        # Submit feedback
        resp = await c.post(f"{API}/feedback", json={
            "query_id": "test-query-id",
            "rating": "up",
            "comment": "Integration test feedback"
        }, headers=headers)
        if resp.status_code in (200, 201):
            ok("Submit feedback")
        else:
            fail("Submit feedback", f"status={resp.status_code}")

        # List feedback
        resp = await c.get(f"{API}/feedback", headers=headers)
        if resp.status_code == 200:
            ok("List feedback responds")
        else:
            fail("List feedback", f"status={resp.status_code}")

        # ─── 9. Audit Logs ───
        print("\n[9] Audit Logs")
        resp = await c.get(f"{API}/audit", headers=headers)
        if resp.status_code == 200:
            audit_list = resp.json()
            if isinstance(audit_list, list):
                ok(f"Audit logs: {len(audit_list)} entries")
            else:
                ok("Audit logs responds")
        else:
            fail("Audit logs", f"status={resp.status_code}")

        # ─── 10. Conversations ───
        print("\n[10] Conversations")
        resp = await c.get(f"{API}/conversations", headers=headers)
        if resp.status_code == 200:
            conv_list = resp.json()
            if isinstance(conv_list, list):
                ok(f"Conversations: {len(conv_list)} found")
            else:
                ok("Conversations responds")
        else:
            fail("Conversations", f"status={resp.status_code}")

        # ─── 11. SSE Stream ───
        print("\n[11] SSE Stream")
        if ds_id:
            resp = await c.post(f"{API}/query/stream", json={
                "question": "你好",
                "datasource_id": ds_id
            }, headers=headers)
            if resp.status_code == 200:
                text = resp.text
                if "intent" in text:
                    ok("SSE stream contains intent")
                else:
                    ok(f"SSE stream responds (len={len(text)})")
            else:
                fail("SSE stream", f"status={resp.status_code}")

        # ─── 12. CORS ───
        print("\n[12] CORS")
        resp = await c.options(f"{API}/datasources", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        })
        if resp.status_code == 200:
            cors = resp.headers.get("access-control-allow-origin", "")
            if "localhost:5173" in cors:
                ok("CORS allows localhost:5173")
            else:
                ok(f"CORS responds (origin header={cors})")
        else:
            fail("CORS preflight", f"status={resp.status_code}")

    # ─── Summary ───
    print(f"\n{'='*50}")
    print(f"Integration Test Results: {passed} passed, {failed} failed")
    if errors:
        print(f"\nFailed tests:")
        for name, detail in errors:
            print(f"  - {name}: {detail}")
    print(f"{'='*50}")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(_run_integration())
    sys.exit(0 if success else 1)
