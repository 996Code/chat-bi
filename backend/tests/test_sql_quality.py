"""SQL 质量校验测试：针对 15 个复杂查询，校验生成 SQL 的结构正确性。

Run: .venv/bin/python -m pytest tests/test_sql_quality.py -v
"""
import asyncio
import json
import re
from httpx import AsyncClient

import pytest

BASE = "http://127.0.0.1:8999"
API = "/chat-bi/api/v1"
EMAIL = "admin@chatbi.com"
PWD = "Test1234!"
DS = "461bddad-ecbe-4756-aaa7-02446b841a5d"

# Known valid tables in the chatbi datasource
VALID_TABLES = frozenset({
    "t_orders", "t_users", "t_products", "t_order_items", "t_payments",
    "t_shipping", "t_categories", "t_coupons", "t_inventory", "t_reviews",
    "t_warehouses", "orders", "users", "products", "payments",
    "order_items", "shipping",
})

# Regex: extract table names from FROM/JOIN clauses
_SQL_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
    re.IGNORECASE,
)


def extract_sql_tables(sql: str) -> list[str]:
    """Extract table names from SQL."""
    return [t for t in _SQL_TABLE_RE.findall(sql)
            if t.lower() not in {'select', 'where', 'set', 'values', 'into',
                                  'group', 'order', 'having', 'limit', 'on'}]


def has_chinese(sql: str) -> bool:
    """Check if SQL contains Chinese characters."""
    return any('一' <= c <= '鿿' for c in sql)


# Query definitions with expected validation criteria
QUERY_CASES = [
    {
        "question": "有多少用户",
        "expect_tables": {"t_users", "users"},
        "expect_aggregation": True,  # should have COUNT/SUM/AVG
        "min_row_count": 1,
    },
    {
        "question": "各城市的订单数量",
        "expect_tables": {"t_orders", "orders"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 1,
    },
    {
        "question": "各品类的销售总额",
        "expect_tables": {"t_products", "t_categories", "products", "categories"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 1,
    },
    {
        "question": "各快递公司的平均配送时长",
        "expect_tables": {"t_shipping", "shipping"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 1,
    },
    {
        "question": "销售额排名前十的商品",
        "expect_tables": {"t_products", "t_order_items", "products", "order_items"},
        "expect_aggregation": True,
        "expect_order_by": True,
        "expect_limit": True,
        "min_row_count": 1,
    },
    {
        "question": "各仓库的库存商品总价值",
        "expect_tables": {"t_inventory", "t_warehouses", "inventory", "warehouses"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 1,
    },
    {
        "question": "不同支付方式的销售占比",
        "expect_tables": {"t_payments", "t_orders", "payments", "orders"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 1,
    },
    {
        "question": "VIP用户的平均订单金额",
        "expect_tables": {"t_users", "t_orders", "users", "orders"},
        "expect_aggregation": True,
        "min_row_count": 0,  # might be 0 if no VIP users
    },
    {
        "question": "库存不足的商品有哪些",
        "expect_tables": {"t_inventory", "t_products", "inventory", "products"},
        "expect_where": True,
        "min_row_count": 0,
    },
    {
        "question": "优惠券的使用情况",
        "expect_tables": {"t_coupons", "coupons"},
        "expect_aggregation": False,  # could be a simple SELECT
        "min_row_count": 0,
    },
    {
        "question": "复购率是多少",
        "expect_tables": {"t_orders", "orders"},
        "expect_aggregation": True,
        "min_row_count": 1,
    },
    {
        "question": "最近3个月的订单趋势",
        "expect_tables": {"t_orders", "orders"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 0,
    },
    {
        "question": "没有下过单的用户有哪些",
        "expect_tables": {"t_users", "t_orders", "users", "orders"},
        "expect_exists_or_join": True,  # should use NOT EXISTS or LEFT JOIN ... IS NULL
        "min_row_count": 0,
    },
    {
        "question": "各月份的销售额对比",
        "expect_tables": {"t_orders", "orders"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 0,
    },
    {
        "question": "退款订单的支付方式分布",
        "expect_tables": {"t_orders", "t_payments", "orders", "payments"},
        "expect_aggregation": True,
        "expect_group_by": True,
        "min_row_count": 0,
    },
]


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def auth_token():
    async with AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD})
        r.raise_for_status()
        return r.json()["access_token"]


@pytest.fixture(scope="module")
async def client():
    async with AsyncClient(base_url=BASE, timeout=60) as c:
        yield c


async def _query(client: AsyncClient, question: str, token: str, delay: float = 2.5) -> dict:
    """Execute a query and return response."""
    await asyncio.sleep(delay)
    r = await client.post(
        f"{API}/query",
        json={"question": question, "datasource_id": DS},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()


@pytest.mark.parametrize("case", QUERY_CASES, ids=[c["question"] for c in QUERY_CASES])
@pytest.mark.asyncio
async def test_sql_quality(client, auth_token, case):
    """Validate SQL quality for a complex query."""
    question = case["question"]
    expect_tables = case.get("expect_tables", set())

    result = await _query(client, question, auth_token)

    # 1. Must be successful
    assert result.get("success"), f"Query failed: {result.get('error')}"

    sql = result.get("sql", "")
    assert sql, "SQL is empty"

    # 2. No Chinese characters in SQL
    assert not has_chinese(sql), f"SQL contains Chinese: {sql[:200]}"

    # 3. Table names must be valid
    used_tables = set(extract_sql_tables(sql))
    invalid = used_tables - VALID_TABLES
    assert not invalid, f"SQL uses invalid tables: {invalid}. Valid: {VALID_TABLES}"

    # 4. Expected tables present
    if expect_tables:
        found = used_tables & expect_tables
        assert found, f"Expected one of {expect_tables} in SQL, but used: {used_tables}"

    # 5. Aggregation check
    if case.get("expect_aggregation"):
        has_agg = any(kw in sql.upper() for kw in ["COUNT(", "SUM(", "AVG(", "MAX(", "MIN("])
        assert has_agg, f"Expected aggregation in SQL for '{question}', got: {sql[:200]}"

    # 6. GROUP BY check
    if case.get("expect_group_by"):
        assert "GROUP BY" in sql.upper(), f"Expected GROUP BY in SQL for '{question}'"

    # 7. ORDER BY check
    if case.get("expect_order_by"):
        assert "ORDER BY" in sql.upper(), f"Expected ORDER BY in SQL for '{question}'"

    # 8. LIMIT check
    if case.get("expect_limit"):
        assert "LIMIT" in sql.upper(), f"Expected LIMIT in SQL for '{question}'"

    # 9. WHERE/JOIN check
    if case.get("expect_where"):
        has_where_or_join = "WHERE" in sql.upper() or "JOIN" in sql.upper()
        assert has_where_or_join, f"Expected WHERE/JOIN in SQL for '{question}'"

    # 10. EXISTS/LEFT JOIN for "没有..." pattern
    if case.get("expect_exists_or_join"):
        has_not_exists = "NOT" in sql.upper() and "EXISTS" in sql.upper()
        has_left_join_null = "LEFT JOIN" in sql.upper() and "IS NULL" in sql.upper()
        has_not_in = "NOT" in sql.upper() and "IN" in sql.upper()
        assert has_not_exists or has_left_join_null or has_not_in, \
            f"Expected anti-join pattern for '{question}': {sql[:200]}"

    # 11. Row count check
    row_count = result.get("row_count", 0)
    min_rows = case.get("min_row_count", 0)
    assert row_count >= min_rows, f"Expected at least {min_rows} rows, got {row_count}"


@pytest.mark.asyncio
async def test_intent_non_query():
    """Verify non-data-query questions are classified as Other."""
    from app.ai.nodes.intent import classify_intent

    # These should be classified as Other
    other_questions = [
        "你好",
        "hello",
        "谢谢",
        "你是谁",
        "你能做什么",
        "hi",
        "再见",
    ]
    for q in other_questions:
        intent = await classify_intent(q)
        assert intent == "Other", f"Expected Other for '{q}', got {intent}"


@pytest.mark.asyncio
async def test_intent_data_query():
    """Verify data-query questions are classified as DataQuery."""
    from app.ai.nodes.intent import classify_intent

    query_questions = [
        "有多少用户",
        "各城市的订单数量",
        "销售额排名前十的商品",
        "最近3个月的订单趋势",
        "各快递公司的平均配送时长",
    ]
    for q in query_questions:
        intent = await classify_intent(q)
        assert intent == "DataQuery", f"Expected DataQuery for '{q}', got {intent}"
