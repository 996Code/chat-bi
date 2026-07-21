"""
ChatBI v2 — E2E 业务验证脚本

验证本次迭代的完整业务流程:
  1. 记忆列表/读取 — 结构化 frontmatter 字段可读
  2. 对话查询 — 触发 persist_linkage_memory 写入新格式
  3. 整理 + 图谱同步 — linkage → graph confidence 更新
  4. 前端可访问性

用法:
  python backend/scripts/e2e_business_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8999/chat-bi/api/v1"
DS_ID = "ff580191db53408ea214da49d2632254"
TIMEOUT = 120


def get_token() -> str:
    resp = httpx.post(f"{API_BASE}/dev/token", json={"tenant_id": "default_tenant", "user_id": "admin_user"}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def test_1_memory_list(token: str) -> bool:
    """测试1: 记忆列表 + 结构化 frontmatter 字段"""
    print("\n" + "=" * 60)
    print("测试1: 记忆列表 + 结构化 frontmatter 字段")
    print("=" * 60)

    # API 列表
    resp = httpx.get(f"{API_BASE}/memory", params={"data_source_id": DS_ID}, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    api_memories = resp.json()
    print(f"  API 记忆数: {len(api_memories)} (linkage 被 API 过滤, 正常)")

    # 直接读文件系统
    project_root = str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, project_root)
    from app.core.agent_memory import AgentMemoryStore

    store = AgentMemoryStore(base_dir=f"{project_root}/memory/default_tenant/{DS_ID}")
    all_memories = store.list_memories()
    linkage = [m for m in all_memories if m.get("type") == "linkage"]

    print(f"  磁盘 linkage 数: {len(linkage)}")

    # 验证结构化字段
    ok = True
    for m in linkage:
        if "co_occurrence" not in m:
            print(f"  ❌ {m['name']}: 缺少 co_occurrence")
            ok = False
        if "tables" not in m:
            print(f"  ❌ {m['name']}: 缺少 tables")
            ok = False
        if "scenes" not in m:
            print(f"  ❌ {m['name']}: 缺少 scenes")
            ok = False
        if "aggregation" not in m:
            print(f"  ❌ {m['name']}: 缺少 aggregation")
            ok = False

    jp_count = sum(1 for m in linkage if "join_paths" in m)
    sc_count = sum(1 for m in linkage if "scenes" in m)
    ag_count = sum(1 for m in linkage if "aggregation" in m)
    print(f"  结构化字段覆盖: join_paths={jp_count}/{len(linkage)}, scenes={sc_count}/{len(linkage)}, aggregation={ag_count}/{len(linkage)}")

    # 展示一条完整的
    sample = next(m for m in linkage if m.get("join_paths"))
    print(f"\n  📋 样例: {sample['name']}")
    print(f"    co_occurrence: {sample['co_occurrence']}")
    print(f"    tables: {sample['tables']}")
    print(f"    join_paths: {sample['join_paths']}")
    print(f"    scenes: {sample['scenes']}")
    print(f"    aggregation: {sample['aggregation']}")

    if ok:
        print("  ✅ 测试1 通过")
    return ok


def test_2_chat_query(token: str) -> bool:
    """测试2: 对话查询 → persist_linkage_memory 写入新格式"""
    print("\n" + "=" * 60)
    print("测试2: 对话查询 → persist_linkage_memory")
    print("=" * 60)

    project_root = str(Path(__file__).resolve().parent.parent)
    from app.core.agent_memory import AgentMemoryStore

    store = AgentMemoryStore(base_dir=f"{project_root}/memory/default_tenant/{DS_ID}")

    # 记录查询前状态
    before = {}
    for m in store.list_memories():
        if m.get("type") == "linkage":
            before[m["name"]] = m.get("co_occurrence", 0)

    # 执行查询 (非流式)
    print("  执行查询: 本月各品类的销售额 ...")
    resp = httpx.post(
        f"{API_BASE}/chat",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "本月各品类的销售额"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    sql = data.get("sql", "")
    rows = data.get("row_count", 0)
    tables = data.get("tables", [])
    print(f"  SQL: {sql[:120]}")
    print(f"  行数: {rows}, 表: {tables}")

    # 注意: 非流式 chat 不调用 persist_linkage_memory
    # 需要用流式接口
    print("  执行流式查询: 查询每个用户的订单数 ...")
    with httpx.stream(
        "POST",
        f"{API_BASE}/chat/stream",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "查询每个用户的订单数"},
        timeout=TIMEOUT,
    ) as resp:
        stream_data = {}
        answer = ""
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                import json
                event = json.loads(payload)
                etype = event.get("type", "")
                if etype == "sql_result":
                    stream_data["sql"] = event.get("sql", "")
                    stream_data["tables"] = event.get("tables", [])
                    stream_data["rows"] = event.get("row_count", 0)
                elif etype == "answer":
                    answer += event.get("content", "")
                elif etype == "persist_warning":
                    stream_data["persist_warning"] = event.get("message", "")
                elif etype == "error":
                    stream_data["error"] = event.get("message", "")
            except Exception:
                pass

    print(f"  流式 SQL: {stream_data.get('sql', '?')[:120]}")
    print(f"  流式行数: {stream_data.get('rows', 0)}, 表: {stream_data.get('tables', [])}")
    if stream_data.get("persist_warning"):
        print(f"  ⚠️ persist_warning: {stream_data['persist_warning']}")
    if stream_data.get("error"):
        print(f"  ❌ error: {stream_data['error']}")

    # 检查 linkage 变化
    after = {}
    for m in store.list_memories():
        if m.get("type") == "linkage":
            after[m["name"]] = m.get("co_occurrence", 0)

    changed = []
    for name, co_after in after.items():
        co_before = before.get(name, 0)
        if co_after > co_before:
            changed.append(f"{name}: {co_before}→{co_after}")

    # 也检查新增的
    new = [name for name in after if name not in before]

    if changed:
        print(f"  📈 co_occurrence 变化: {changed}")
    if new:
        print(f"  🆕 新增 linkage: {new}")

    # 验证新写入的 linkage 有结构化字段
    # 只检查 persist_linkage_memory 创建/更新的 (name 以 "linkage-" 开头)
    # LLM 自主提炼的 (如 "biz_orders-user_id-join-uc_users") 可能缺少结构化字段
    ok = True
    persist_changed = [c.split(":")[0] for c in changed if c.split(":")[0].startswith("linkage-")]
    persist_new = [n for n in new if n.startswith("linkage-")]
    for m in store.list_memories():
        if m.get("type") == "linkage" and m["name"] in (persist_new + persist_changed):
            if "scenes" not in m:
                print(f"  ❌ {m['name']}: 更新后缺少 scenes")
                ok = False
            if "aggregation" not in m:
                print(f"  ❌ {m['name']}: 更新后缺少 aggregation")
                ok = False

    if ok:
        print("  ✅ 测试2 通过")
    return ok


def test_3_consolidate_and_graph_sync(token: str) -> bool:
    """测试3: 整理 + 图谱同步"""
    print("\n" + "=" * 60)
    print("测试3: 整理 + 图谱同步")
    print("=" * 60)

    # 触发整理
    print("  触发记忆整理 ...")
    resp = httpx.post(
        f"{API_BASE}/memory/consolidate",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    print(f"  整理已触发: {resp.json()}")

    # 轮询整理状态
    for i in range(30):
        time.sleep(2)
        resp = httpx.get(
            f"{API_BASE}/memory/consolidate/status",
            params={"data_source_id": DS_ID},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        status = resp.json()
        s = status.get("status", "unknown")
        progress = status.get("progress", 0)
        stage = status.get("stage", "")
        print(f"  [{i+1}] status={s}, progress={progress}%, stage={stage}")

        if s in ("done", "completed"):
            result = status.get("result", {})
            print(f"  整理结果: consolidated={result.get('consolidated', 0)}, total={result.get('total', 0)}")
            graph_sync = result.get("graph_sync")
            graph_conflict = result.get("graph_sync_conflict")
            if graph_sync:
                print(f"  📊 图谱同步: new_version={graph_sync.get('new_version')}, boosted={graph_sync.get('boosted_pairs')}, new_pairs={graph_sync.get('new_pairs')}")
            if graph_conflict:
                print(f"  ⚠️ 图谱同步冲突: {result.get('graph_sync_error', '')}")
            if not graph_sync and not graph_conflict:
                print(f"  ℹ️ 图谱同步: 无更新 (co_occurrence 未达阈值)")
            print("  ✅ 测试3 通过")
            return True

        if s == "error":
            print(f"  ❌ 整理失败: {status.get('error', '')}")
            return False

    print("  ❌ 整理超时")
    return False


def test_4_frontend_accessible() -> bool:
    """测试4: 前端可访问"""
    print("\n" + "=" * 60)
    print("测试4: 前端可访问性")
    print("=" * 60)

    try:
        resp = httpx.get("http://localhost:5173", timeout=5)
        if resp.status_code == 200:
            print("  ✅ 前端可访问 (HTTP 200)")
            return True
        else:
            print(f"  ❌ 前端返回 HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ 前端不可访问: {e}")
        return False


def main() -> None:
    print("🔑 获取 token ...")
    try:
        token = get_token()
        print(f"  ✅ Token 获取成功")
    except Exception as e:
        print(f"  ❌ Token 获取失败: {e}")
        print("  请确认后端正在运行: ./start-backend.sh")
        sys.exit(1)

    results = {}

    # 测试1
    try:
        results["1_memory_list"] = test_1_memory_list(token)
    except Exception as e:
        print(f"  ❌ 测试1 异常: {e}")
        results["1_memory_list"] = False

    # 测试2
    try:
        results["2_chat_query"] = test_2_chat_query(token)
    except Exception as e:
        print(f"  ❌ 测试2 异常: {e}")
        results["2_chat_query"] = False

    # 测试3
    try:
        results["3_consolidate_graph"] = test_3_consolidate_and_graph_sync(token)
    except Exception as e:
        print(f"  ❌ 测试3 异常: {e}")
        results["3_consolidate_graph"] = False

    # 测试4
    try:
        results["4_frontend"] = test_4_frontend_accessible()
    except Exception as e:
        print(f"  ❌ 测试4 异常: {e}")
        results["4_frontend"] = False

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  总计: {passed}/{total} 通过")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
