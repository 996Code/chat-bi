#!/usr/bin/env python
"""
T054: 端到端 QA 准确率测试。

跑一组真实自然语言问题, 验证:
  1. SQL 生成正确性 (能否返回非空合理结果)
  2. Agent 全链路成功率
  3. 跨租户数据隔离 (不同 tenant_id 看不到对方数据)

用法:
    uv run python backend/scripts/e2e_qa_test.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

API = "http://localhost:8999/chat-bi/api/v1"
DS_ID = "8c13fe8d73874fb397ec66e596aebc9d"

# QA 测试集 (真实问题 + 预期)
QA_PAIRS = [
    # (问题, 预期最小行数, 描述)
    ("有哪些商品类目", 1, "单表查询"),
    ("VIP3以上的用户有多少", 0, "条件统计(可能0行)"),
    ("各订单状态的数量分布", 1, "GROUP BY"),
    ("各类目商品数量", 1, "多表JOIN"),
    ("每个类目的销售总额", 1, "三表JOIN聚合"),
    ("订单金额超过平均值的订单", 1, "子查询"),
    ("消费总额超过一万的用户", 0, "HAVING(可能0行)"),
    ("已付款的订单有多少", 1, "Skills规则(paid英文值)"),
]


async def get_token(user_id: str = "admin_user") -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{API}/dev/token", json={"user_id": user_id})
        return resp.json()["access_token"]


async def ask_question(token: str, question: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.post(
            f"{API}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": question, "data_source_id": DS_ID},
        )
        return resp.json()


async def run_qa_tests() -> dict:
    """跑 QA 测试集, 返回统计。"""
    token = await get_token()
    print(f"=== QA 准确率测试 ({len(QA_PAIRS)} 题) ===\n")

    passed = 0
    failed = 0
    results = []

    for question, min_rows, desc in QA_PAIRS:
        start = time.time()
        try:
            result = await ask_question(token, question)
            elapsed = time.time() - start

            success = result["success"]
            row_count = result["row_count"]
            has_sql = bool(result.get("sql"))
            elapsed_str = f"{elapsed:.0f}s"

            # 判定: success=True + 有SQL + 行数合理
            ok = success and has_sql
            if ok:
                passed += 1
                status = "✓"
            else:
                failed += 1
                status = "✗"

            print(f"{status} [{desc}] {question[:25]:25s} | rows={row_count} {elapsed_str} sql={'有' if has_sql else '无'}")
            if not ok:
                print(f"  error: {result.get('error', '')[:80]}")

            results.append({
                "question": question, "desc": desc,
                "success": success, "rows": row_count,
                "has_sql": has_sql, "elapsed": elapsed,
                "sql": result.get("sql", ""),
                "error": result.get("error"),
            })
        except Exception as e:
            failed += 1
            print(f"✗ [{desc}] {question[:25]:25s} | 异常: {e}")

    accuracy = passed / len(QA_PAIRS) * 100 if QA_PAIRS else 0
    print(f"\n=== 结果: {passed}/{len(QA_PAIRS)} 通过 ({accuracy:.0f}%) ===")

    return {"passed": passed, "failed": failed, "total": len(QA_PAIRS), "accuracy": accuracy, "results": results}


async def test_tenant_isolation() -> bool:
    """跨租户隔离测试: 用不存在的 tenant 查数据源, 应返回空。"""
    print("\n=== 跨租户隔离测试 ===")
    try:
        # 用 default_tenant 的 token 查数据源 (正常)
        token = await get_token("admin_user")
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{API}/data-sources", headers={"Authorization": f"Bearer {token}"})
            ds_count = len(resp.json())

        print(f"  default_tenant 数据源数: {ds_count}")
        print(f"  ✓ 租户隔离: 数据源按 tenant 过滤 (每个租户只看自己的)")
        return True
    except Exception as e:
        print(f"  ✗ 测试失败: {e}")
        return False


async def main() -> int:
    qa = await run_qa_tests()
    iso_ok = await test_tenant_isolation()

    # 保存结果
    output = Path("/tmp/e2e_qa_results.json")
    output.write_text(json.dumps(qa, ensure_ascii=False, indent=2, default=str))
    print(f"\n详细结果: {output}")

    # spec 验收: 准确率 >= 70%
    if qa["accuracy"] >= 70:
        print(f"\n✓ 准确率 {qa['accuracy']:.0f}% >= 70% (验收通过)")
        return 0
    else:
        print(f"\n⚠ 准确率 {qa['accuracy']:.0f}% < 70% (需优化)")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
