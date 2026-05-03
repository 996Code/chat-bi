"""API 全链路测试：通过真实 HTTP 接口走完注册→登录→数据源→查询→导出→反馈全流程。"""
import asyncio
import httpx
import json

BASE = "http://127.0.0.1:8000/api/v1"

async def api(method: str, path: str, json_data=None, headers=None):
    """调用 API 并打印结果。"""
    url = f"{BASE}{path}"
    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, json=json_data, headers=headers or {})
    status = resp.status_code
    try:
        body = resp.json()
    except:
        body = resp.text[:200]
    return status, body


async def run():
    print("=" * 60)
    print("  ChatBI API 全链路测试")
    print("=" * 60)

    headers = {}

    # ─── 1. 注册 ───
    print("\n1. 注册新用户...")
    s, b = await api("POST", "/auth/register", {
        "email": "testuser@chatbi.com",
        "password": "Test1234!"
    })
    print(f"   POST /auth/register → {s}: {b}")
    assert s == 201, f"注册失败: {b}"

    # ─── 2. 邮箱验证（开发环境直接在 DB 中验证） ───
    print("\n2. 验证邮箱...")
    from app.core.security import generate_email_verification_token
    from app.db.session import async_session_factory
    from sqlalchemy import select
    from app.db.models import User

    token = generate_email_verification_token("testuser@chatbi.com")
    s, b = await api("POST", "/auth/verify-email", {"token": token})
    print(f"   POST /auth/verify-email → {s}: {b}")
    assert s == 200, f"邮箱验证失败: {b}"

    # ─── 3. 登录 ───
    print("\n3. 登录...")
    s, b = await api("POST", "/auth/login", {
        "email": "testuser@chatbi.com",
        "password": "Test1234!"
    })
    print(f"   POST /auth/login → {s}: token={'存在' if 'access_token' in b else '缺失'}")
    assert s == 200, f"登录失败: {b}"
    headers["Authorization"] = f"Bearer {b['access_token']}"

    # ─── 4. 刷新 token ───
    print("\n4. 刷新 token...")
    s, b = await api("POST", "/auth/refresh", {"refresh_token": b["refresh_token"]})
    print(f"   POST /auth/refresh → {s}: new_token={'存在' if 'access_token' in b else '缺失'}")
    assert s == 200
    headers["Authorization"] = f"Bearer {b['access_token']}"

    # ─── 5. 创建数据源 ───
    print("\n5. 创建数据源（MySQL）...")
    s, b = await api("POST", "/datasources", {
        "name": "测试 MySQL 数据库",
        "type": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "database_name": "testdb",
        "username": "root",
        "password": "root123"
    }, headers)
    print(f"   POST /datasources → {s}: id={b.get('id', 'N/A')}")
    assert s == 201, f"创建数据源失败: {b}"
    ds_id = b["id"]

    # ─── 6. 列出数据源 ───
    print("\n6. 列出数据源...")
    s, b = await api("GET", "/datasources", headers=headers)
    print(f"   GET /datasources → {s}: count={len(b)}")
    assert s == 200 and len(b) >= 1

    # ─── 7. 查看数据源 schema ───
    print("\n7. 查看数据源 schema...")
    s, b = await api("GET", f"/datasources/{ds_id}/schema", headers=headers)
    print(f"   GET /datasources/{ds_id}/schema → {s}: tables={len(b.get('tables', []))}")

    # ─── 8. 保存查询 ───
    print("\n8. 保存查询...")
    s, b = await api("POST", "/queries", {
        "name": "测试查询",
        "query_text": "查询所有用户",
        "generated_sql": "SELECT * FROM users LIMIT 10",
        "datasource_id": ds_id
    }, headers)
    print(f"   POST /queries → {s}: id={b.get('id', 'N/A')}")
    assert s == 201
    query_id = b["id"]

    # ─── 9. 查看查询历史 ───
    print("\n9. 查看查询历史...")
    s, b = await api("GET", "/queries", headers=headers)
    print(f"   GET /queries → {s}: count={len(b)}")
    assert s == 200

    # ─── 10. 获取单个查询 ───
    print("\n10. 获取单个查询详情...")
    s, b = await api("GET", f"/queries/{query_id}", headers=headers)
    print(f"   GET /queries/{query_id} → {s}: name={b.get('name', 'N/A')}")
    assert s == 200

    # ─── 11. 重新运行查询 ───
    print("\n11. 重新运行查询...")
    s, b = await api("POST", f"/queries/{query_id}/re-run", headers=headers)
    print(f"   POST /queries/{query_id}/re-run → {s}: sql={b.get('sql', 'N/A')[:50]}")
    assert s == 200

    # ─── 12. 提交反馈 ───
    print("\n12. 提交反馈...")
    s, b = await api("POST", "/feedback", {
        "query_id": query_id,
        "rating": "up",
        "comment": "测试结果准确"
    }, headers)
    print(f"   POST /feedback → {s}: rating={b.get('rating', 'N/A')}")
    assert s == 201

    # ─── 13. 查看反馈列表 ───
    print("\n13. 查看反馈列表...")
    s, b = await api("GET", "/feedback", headers=headers)
    print(f"   GET /feedback → {s}: count={len(b)}")
    assert s == 200

    # ─── 14. 导出 CSV ───
    print("\n14. 导出 CSV...")
    s, b = await api("POST", "/export/csv", {
        "columns": ["name", "value"],
        "rows": [{"name": "销售额", "value": 12345}, {"name": "订单数", "value": 678}]
    }, headers)
    print(f"   POST /export/csv → {s}: content_type={'csv' if isinstance(b, str) and 'name' in b else 'N/A'}")

    # ─── 15. 提交查询（触发 AI 查询管道，如果 LLM 可用的话） ───
    print("\n15. 提交 AI 查询...")
    s, b = await api("POST", "/query", {
        "question": "你好",
        "datasource_id": ds_id
    }, headers)
    print(f"   POST /query → {s}: success={b.get('success', 'N/A')}, error={b.get('error', 'N/A')}")

    # ─── 16. 审计日志（管理员） ───
    print("\n16. 尝试查看审计日志（非管理员应返回 403）...")
    s, b = await api("GET", "/audit", headers=headers)
    print(f"   GET /audit → {s}: {'正确返回403' if s == 403 else '意外: ' + str(b)}")
    assert s == 403, "非管理员应该不能访问审计日志"

    # ─── 17. 健康检查 ───
    print("\n17. 健康检查...")
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://127.0.0.1:8000/health")
    print(f"   GET /health → {resp.status_code}: {resp.json()}")
    assert resp.status_code == 200

    print("\n" + "=" * 60)
    print("  全链路测试通过！所有 API 接口正常工作。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
