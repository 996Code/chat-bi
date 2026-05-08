"""
SQL 质量集成测试 — 需要运行中的后端服务器。

启动后端后运行：
  pytest tests/test_sql_quality.py -v -m integration

此测试不走 conftest 的 SQLite 环境，直接请求真实后端。
"""
import asyncio
import os

import pytest
import httpx

BASE = os.getenv("TEST_BASE_URL", "http://localhost:8999")
API = f"{BASE}/chat-bi/api/v1"
EMAIL = os.getenv("TEST_EMAIL", "admin@chatbi.com")
PWD = os.getenv("TEST_PWD", "Test1234!")


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def live_token():
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD})
        assert r.status_code == 200, f"Login failed: {r.text}"
        return r.json()["access_token"]


@pytest.fixture(scope="module")
async def live_datasource_id(live_token):
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.get(f"{API}/datasources", headers={"Authorization": f"Bearer {live_token}"})
        assert r.status_code == 200, f"Get datasources failed: {r.text}"
        data = r.json()
        assert len(data) > 0, "No datasources found — run seed.py first"
        return data[0]["id"]


@pytest.fixture(scope="module")
async def live_client():
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        yield c


QUESTIONS = [
    "各城市用户数量",
    "各部门平均薪资",
    "最近7天每日订单量",
    "销售额前10的产品",
    "各渠道转化率",
]


@pytest.mark.integration
@pytest.mark.asyncio(scope="module")
@pytest.mark.parametrize("question", QUESTIONS)
async def test_sql_generation(live_client, live_token, live_datasource_id, question):
    headers = {"Authorization": f"Bearer {live_token}"}
    r = await live_client.post(
        f"{API}/query/stream",
        json={"question": question, "datasource_id": live_datasource_id},
        headers=headers,
    )
    assert r.status_code == 200, f"Status {r.status_code}: {r.text[:200]}"
    body = r.text
    assert "sql" in body.lower() or "SELECT" in body, f"No SQL in response: {body[:300]}"
