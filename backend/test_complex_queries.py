#!/usr/bin/env python3
"""
ChatBI Complex SQL Query Test Suite
Tests 20 complex queries across 4 categories against the ChatBI API.
"""

import httpx
import json
import time
import sys
from datetime import datetime

BASE_URL = "http://127.0.0.1:8999"
API_PREFIX = "/chat-bi/api/v1"
LOGIN_EMAIL = "admin@chatbi.com"
LOGIN_PASSWORD = "Test1234!"
TIMEOUT = 120.0  # seconds per query

TEST_QUERIES = {
    "A. 存在性判断 (EXISTS / NOT EXISTS)": [
        ("A1", "找出没有下过单的用户"),
        ("A2", "有没有商品没有被评论过"),
        ("A3", "哪些仓库没有库存记录"),
        ("A4", "有没有超过5万元的订单"),
    ],
    "B. 同比环比 (Year-over-year, Month-over-month)": [
        ("B1", "最近3个月的订单趋势"),
        ("B2", "各月份的销售额对比"),
        ("B3", "本月和上月的订单数量对比"),
        ("B4", "各季度的销售趋势"),
    ],
    "C. 复杂统计 (Complex Aggregations)": [
        ("C1", "各品类的订单金额和平均客单价"),
        ("C2", "VIP用户的消费行为分析"),
        ("C3", "不同支付方式的销售占比"),
        ("C4", "各快递公司的平均配送时长"),
        ("C5", "复购率是多少"),
        ("C6", "各城市的用户数和订单密度"),
        ("C7", "库存不足的商品有哪些"),
    ],
    "D. 多表关联 (Multi-table JOINs)": [
        ("D1", "买了iPhone的用户还买了什么"),
        ("D2", "各仓库的库存商品总价值"),
        ("D3", "退款订单的支付方式分布"),
        ("D4", "评分最高的商品及其销量"),
        ("D5", "优惠券的使用情况"),
    ],
}


def login(client):
    """Login and return JWT token."""
    print("=" * 70)
    print("STEP 1: Login")
    print("=" * 70)
    resp = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
    )
    data = resp.json()
    if resp.status_code == 200 and data.get("access_token"):
        token = data["access_token"]
        print(f"  Token obtained: {token[:20]}...")
        return token
    else:
        print(f"  FAIL: {resp.status_code} {data}")
        sys.exit(1)


def find_mysql_datasource(client, token):
    """Find the MySQL test datasource."""
    print("\n" + "=" * 70)
    print("STEP 2: Find MySQL Test Datasource")
    print("=" * 70)
    resp = client.get(
        f"{API_PREFIX}/datasources",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    if resp.status_code != 200:
        print(f"  FAIL: {resp.status_code} {data}")
        sys.exit(1)

    datasources = data if isinstance(data, list) else data.get("datasources", [])
    for ds in datasources:
        ds_type = ds.get("type", "").lower()
        ds_name = ds.get("name", "")
        print(f"  Found: {ds_name} (type={ds_type}, id={ds.get('id')})")
        if ds_type == "mysql" and "测试" in ds_name:
            print(f"  -> Selected datasource: {ds_name} (id={ds['id']})")
            return ds["id"]

    # Fallback: first mysql datasource
    for ds in datasources:
        if ds.get("type", "").lower() == "mysql":
            print(f"  -> Fallback to: {ds.get('name')} (id={ds['id']})")
            return ds["id"]

    print(f"  FAIL: No MySQL datasource found. Available: {[d.get('name') for d in datasources]}")
    sys.exit(1)


def run_query(client, token, datasource_id, question, query_id):
    """Run a single ChatBI query and return result dict."""
    result = {
        "id": query_id,
        "question": question,
        "status": None,
        "intent": None,
        "sql": None,
        "data": None,
        "error": None,
        "response_time": 0,
        "raw": None,
    }

    start = time.time()
    try:
        resp = client.post(
            f"{API_PREFIX}/query",
            json={"question": question, "datasource_id": datasource_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        result["response_time"] = time.time() - start
        result["raw"] = resp.json()

        if resp.status_code != 200:
            result["status"] = "FAIL"
            result["error"] = f"HTTP {resp.status_code}: {result['raw']}"
            return result

        data = result["raw"]
        result["intent"] = data.get("intent", "unknown")

        if result["intent"] == "DataQuery":
            result["sql"] = data.get("sql", "")
            result["data"] = data.get("data")
            if result["sql"] and result["data"] is not None:
                result["status"] = "PASS"
            elif result["sql"] and result["data"] is None:
                result["status"] = "PARTIAL"
                result["error"] = "SQL generated but no data returned"
            else:
                result["status"] = "FAIL"
                result["error"] = "DataQuery intent but no SQL"
        else:
            result["status"] = "FAIL"
            result["error"] = f"Wrong intent: {result['intent']}"

    except httpx.TimeoutException:
        result["response_time"] = time.time() - start
        result["status"] = "FAIL"
        result["error"] = f"Timeout after {TIMEOUT}s"
    except Exception as e:
        result["response_time"] = time.time() - start
        result["status"] = "FAIL"
        result["error"] = str(e)

    return result


def print_result(r):
    """Print a single query result."""
    icon = {"PASS": "✅", "PARTIAL": "⚠️", "FAIL": "❌"}.get(r["status"], "?")
    print(f"\n  {icon} {r['id']}: [{r['status']}] ({r['response_time']:.1f}s)")
    print(f"     Q: {r['question']}")
    print(f"     Intent: {r['intent']}")
    if r["sql"]:
        sql_preview = r["sql"][:120] + "..." if len(r["sql"]) > 120 else r["sql"]
        print(f"     SQL: {sql_preview}")
    if r["data"] is not None:
        if isinstance(r["data"], list):
            print(f"     Rows: {len(r['data'])}")
            if r["data"] and len(r["data"]) > 0:
                print(f"     Sample: {json.dumps(r['data'][0], ensure_ascii=False)[:100]}")
        elif isinstance(r["data"], dict):
            print(f"     Data keys: {list(r['data'].keys())}")
    if r["error"]:
        print(f"     Error: {r['error'][:150]}")


def main():
    results = []
    summary = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}

    print("ChatBI Complex SQL Query Test Suite")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL: {BASE_URL}")

    with httpx.Client(base_url=BASE_URL) as client:
        token = login(client)
        datasource_id = find_mysql_datasource(client, token)

        total_queries = sum(len(v) for v in TEST_QUERIES.values())
        current = 0

        for category, queries in TEST_QUERIES.items():
            print("\n" + "=" * 70)
            print(f"Category: {category}")
            print("=" * 70)

            for query_id, question in queries:
                current += 1
                print(f"\n[{current}/{total_queries}] Running {query_id}...")
                r = run_query(client, token, datasource_id, question, query_id)
                results.append(r)
                summary[r["status"]] += 1
                print_result(r)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for status, count in summary.items():
        icon = {"PASS": "✅", "PARTIAL": "⚠️", "FAIL": "❌"}.get(status, "?")
        print(f"  {icon} {status}: {count}/{len(results)}")

    print(f"\nPass rate: {summary['PASS']/len(results)*100:.0f}%")

    # Detailed failures
    failures = [r for r in results if r["status"] != "PASS"]
    if failures:
        print("\n" + "-" * 70)
        print("DETAILED FAILURE ANALYSIS")
        print("-" * 70)
        for r in failures:
            print(f"\n{r['id']}: {r['question']}")
            print(f"  Status: {r['status']}")
            print(f"  Intent: {r['intent']}")
            if r["sql"]:
                print(f"  SQL: {r['sql'][:200]}")
            if r["error"]:
                print(f"  Error: {r['error'][:300]}")

    # Save results to file
    output_file = "chatbi_test_results.json"
    with open(output_file, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nFull results saved to: {output_file}")


if __name__ == "__main__":
    main()
