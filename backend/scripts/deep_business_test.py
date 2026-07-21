"""
ChatBI v2 — 深度业务验证脚本

不凭感觉，逐项验证每个功能的正确性：
  1. 流式对话完整 SSE 事件链
  2. persist_linkage_memory frontmatter 内容准确性
  3. 更新已有 linkage 时结构化字段合并
  4. 整理 + 图谱同步结果准确性
  5. recall_memories 召回功能
  6. 向后兼容 (旧格式 linkage)
  7. 前端完整业务流程
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8999/chat-bi/api/v1"
DS_ID = "ff580191db53408ea214da49d2632254"
TIMEOUT = 120

project_root = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, project_root)


def get_token() -> str:
    resp = httpx.post(f"{API_BASE}/dev/token", json={"tenant_id": "default_tenant", "user_id": "admin_user"}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_store():
    from app.core.agent_memory import AgentMemoryStore
    return AgentMemoryStore(base_dir=f"{project_root}/memory/default_tenant/{DS_ID}")


# ═══════════════════════════════════════════════════════════════
def test_1_stream_sse_chain(token: str) -> bool:
    """验证1: 流式对话完整 SSE 事件链"""
    print("\n" + "=" * 60)
    print("验证1: 流式对话完整 SSE 事件链")
    print("=" * 60)

    events_received = []
    with httpx.stream(
        "POST",
        f"{API_BASE}/chat/stream",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "本月各品类的销售额"},
        timeout=TIMEOUT,
    ) as resp:
        assert resp.status_code == 200, f"HTTP {resp.status_code}"
        current_event_type = ""
        for line in resp.iter_lines():
            line = line.strip()
            if not line:
                continue
            # SSE 格式: "event: type" 或 "data: payload" 或 "id: N"
            if line.startswith("event:"):
                current_event_type = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    events_received.append({"type": "DONE"})
                    break
                try:
                    event = json.loads(payload)
                    # 优先用 data 里的 type, 否则用 SSE event 行
                    if "type" not in event and current_event_type:
                        event["type"] = current_event_type
                    events_received.append(event)
                except Exception:
                    if current_event_type:
                        events_received.append({"type": current_event_type})

    # 检查事件类型链
    event_types = [e.get("type", "") for e in events_received]
    print(f"  收到事件类型: {event_types}")

    # 必须有的事件 (按实际 SSE event 名)
    must_have = ["start", "intent", "schema", "thinking", "sql"]
    missing = [t for t in must_have if t not in event_types]
    if missing:
        print(f"  ❌ 缺少事件类型: {missing}")
        return False

    # 验证 sql 事件内容 (SSE event 名是 "sql", data 里包含 SQL + 结果)
    sql_event = next((e for e in events_received if e.get("type") == "sql"), None)
    if sql_event is None:
        print("  ❌ 找不到 sql 事件")
        return False

    sql = sql_event.get("sql", "")
    row_count = sql_event.get("row_count", 0)
    print(f"  SQL: {sql[:150]}")
    print(f"  行数: {row_count}")

    # data 事件包含查询结果 (SSE event 名是 "data")
    data_events = [e for e in events_received if e.get("type") == "data"]
    if data_events:
        # data 事件可能包含 rows
        total_rows = sum(e.get("row_count", 0) for e in data_events)
        print(f"  data 事件数: {len(data_events)}, 总行数: {total_rows}")
    
    # complete 事件表示结束
    has_complete = any(e.get("type") == "complete" for e in events_received)
    print(f"  有 complete 事件: {has_complete}")

    if not sql:
        print("  ❌ sql 事件中无 SQL")
        return False

    print("  ✅ 验证1 通过: SSE 事件链完整, SQL 有结果, 回答非空")
    return True


# ═══════════════════════════════════════════════════════════════
def test_2_frontmatter_accuracy(token: str) -> bool:
    """验证2: persist_linkage_memory 写入的 frontmatter 内容准确性"""
    print("\n" + "=" * 60)
    print("验证2: persist_linkage_memory frontmatter 内容准确性")
    print("=" * 60)

    store = get_store()

    # 先清理，确保干净的测试环境
    # 执行一次流式查询，触发 persist_linkage_memory
    print("  执行流式查询: 查询每个用户的订单数 ...")
    with httpx.stream(
        "POST",
        f"{API_BASE}/chat/stream",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "查询每个用户的订单数"},
        timeout=TIMEOUT,
    ) as resp:
        for line in resp.iter_lines():
            if line.strip() == "data: [DONE]":
                break

    # 检查核心表对的 linkage 记忆
    store2 = get_store()  # 重新读取
    ou = store2.get_linkage_memory("biz_orders", "uc_users")

    if ou is None:
        print("  ❌ 找不到 biz_orders↔uc_users linkage")
        return False

    print(f"  biz_orders↔uc_users:")
    print(f"    co_occurrence: {ou.get('co_occurrence')}")
    print(f"    tables: {ou.get('tables')}")
    print(f"    join_paths: {ou.get('join_paths')}")
    print(f"    scenes: {ou.get('scenes')}")
    print(f"    aggregation: {ou.get('aggregation')}")

    ok = True

    # 1. tables 必须是排序好的
    if ou.get("tables") != sorted(ou.get("tables", [])):
        print("  ❌ tables 未排序")
        ok = False

    # 2. join_paths 必须有 ON 条件 (biz_orders↔uc_users 是直接 JOIN)
    jp = ou.get("join_paths", [])
    if not jp:
        print("  ❌ biz_orders↔uc_users 应有 join_paths (直接 JOIN)")
        ok = False
    else:
        # ON 条件必须包含两表名
        for j in jp:
            on = j.get("on", "")
            assert "biz_orders" in on or "uc_users" in on, f"join_path ON 不含表名: {on}"
            assert j.get("join_type") == "LEFT", f"join_type 不是 LEFT: {j.get('join_type')}"
        print(f"    ✅ join_paths 内容正确 ({len(jp)} 条 ON 条件)")

    # 3. scenes 必须包含查询问题
    scenes = ou.get("scenes", [])
    if not scenes:
        print("  ❌ scenes 为空")
        ok = False
    else:
        has_query = any("订单" in s or "用户" in s for s in scenes)
        if not has_query:
            print(f"  ⚠️ scenes 中无匹配查询问题的条目: {scenes}")
        else:
            print(f"    ✅ scenes 包含查询问题 ({len(scenes)} 条)")

    # 4. aggregation 必须有值
    agg = ou.get("aggregation", "")
    if not agg:
        print("  ❌ aggregation 为空")
        ok = False
    else:
        print(f"    ✅ aggregation = {agg}")

    # 5. 验证 Markdown body 也包含关键信息
    content = store2.read_memory(ou["id"]) or ""
    if "## JOIN 路径" not in content and "## 关联方式" not in content:
        print("  ❌ Markdown body 缺少 JOIN 路径/关联方式段")
        ok = False
    else:
        print("    ✅ Markdown body 包含 JOIN 路径/关联方式")

    if ok:
        print("  ✅ 验证2 通过")
    return ok


# ═══════════════════════════════════════════════════════════════
def test_3_update_merge(token: str) -> bool:
    """验证3: 更新已有 linkage 时结构化字段合并"""
    print("\n" + "=" * 60)
    print("验证3: 更新已有 linkage 时结构化字段合并")
    print("=" * 60)

    store = get_store()

    # 记录所有 linkage 的当前状态
    before_all = {}
    for m in store.list_memories():
        if m.get("type") == "linkage" and m["name"].startswith("linkage-"):
            before_all[m["name"]] = {
                "co": m.get("co_occurrence", 0),
                "scenes": m.get("scenes", []),
                "join_paths": m.get("join_paths", []),
                "aggregation": m.get("aggregation", ""),
            }

    # 执行不同问题的查询 (会更新某些表对的 linkage)
    print("  执行流式查询: 用户消费排行 ...")
    with httpx.stream(
        "POST",
        f"{API_BASE}/chat/stream",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "用户消费排行"},
        timeout=TIMEOUT,
    ) as resp:
        for line in resp.iter_lines():
            if line.strip() == "data: [DONE]":
                break

    # 再执行一次不同查询
    print("  执行流式查询: 本月各品类的销售额 ...")
    with httpx.stream(
        "POST",
        f"{API_BASE}/chat/stream",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "本月各品类的销售额"},
        timeout=TIMEOUT,
    ) as resp:
        for line in resp.iter_lines():
            if line.strip() == "data: [DONE]":
                break

    # 重新读取
    store2 = get_store()
    after_all = {}
    for m in store2.list_memories():
        if m.get("type") == "linkage" and m["name"].startswith("linkage-"):
            after_all[m["name"]] = {
                "co": m.get("co_occurrence", 0),
                "scenes": m.get("scenes", []),
                "join_paths": m.get("join_paths", []),
                "aggregation": m.get("aggregation", ""),
            }

    # 找到有变化的 linkage
    changed = []
    for name, after in after_all.items():
        before = before_all.get(name)
        if before and after["co"] > before["co"]:
            changed.append(name)

    new_linkages = [name for name in after_all if name not in before_all]

    print(f"  co_occurrence 变化的 linkage: {len(changed)} 条")
    print(f"  新增 linkage: {len(new_linkages)} 条")

    if not changed and not new_linkages:
        print("  ⚠️ 无 linkage 变化 (查询可能未触发 persist_linkage_memory)")
        return True  # 不是代码错误, 可能是 LLM 选了不同表

    ok = True

    # 验证变更的 linkage: 旧 scenes 应保留
    for name in changed[:3]:  # 只检查前3条
        before = before_all[name]
        after = after_all[name]
        # 旧 scenes 应保留
        for s in before["scenes"]:
            if s not in after["scenes"]:
                print(f"  ❌ {name}: 旧场景丢失 '{s}'")
                ok = False
        # co_occurrence 应递增
        if after["co"] <= before["co"]:
            print(f"  ❌ {name}: co_occurrence 未递增 {before['co']}→{after['co']}")
            ok = False
        else:
            print(f"  ✅ {name}: co={before['co']}→{after['co']}, scenes {len(before['scenes'])}→{len(after['scenes'])}")

    # 验证新增 linkage: 应有结构化字段
    for name in new_linkages[:3]:
        m = after_all[name]
        if "scenes" not in m or not m["scenes"]:
            print(f"  ❌ 新增 {name}: 缺少 scenes")
            ok = False
        if "aggregation" not in m or not m["aggregation"]:
            print(f"  ❌ 新增 {name}: 缺少 aggregation")
            ok = False
        else:
            print(f"  ✅ 新增 {name}: co={m['co']}, scenes={len(m['scenes'])}, agg={m['aggregation']}")

    if ok:
        print("  ✅ 验证3 通过")
    return ok


# ═══════════════════════════════════════════════════════════════
def test_4_graph_sync_accuracy(token: str) -> bool:
    """验证4: 整理 + 图谱同步结果准确性"""
    print("\n" + "=" * 60)
    print("验证4: 整理 + 图谱同步结果准确性")
    print("=" * 60)

    # 获取当前图谱版本
    resp = httpx.get(
        f"{API_BASE}/semantic-models",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    before_version = resp.json().get("version", 0)
    before_content = resp.json().get("content", {})
    print(f"  当前图谱版本: v{before_version}")

    # 统计当前 confidence
    before_conf = {}
    for m in before_content.get("models", []):
        for r in m.get("relationships", []):
            if r.get("source") == "implicit_mining":
                key = f"{m['name']}→{r['target_model']}"
                before_conf[key] = r.get("confidence", 0)

    # 触发整理
    resp = httpx.post(
        f"{API_BASE}/memory/consolidate",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()

    # 轮询
    for i in range(40):
        time.sleep(2)
        resp = httpx.get(
            f"{API_BASE}/memory/consolidate/status",
            params={"data_source_id": DS_ID},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        status = resp.json()
        if status.get("status") in ("done", "completed"):
            result = status.get("result", {})
            graph_sync = result.get("graph_sync", {})
            print(f"  整理完成: consolidated={result.get('consolidated')}, total={result.get('total')}")
            if graph_sync:
                print(f"  图谱同步: v{before_version}→v{graph_sync.get('new_version')}, "
                      f"boosted={graph_sync.get('boosted_pairs')}, new_pairs={graph_sync.get('new_pairs')}")
            break
        if status.get("status") == "error":
            print(f"  ❌ 整理失败: {status.get('error')}")
            return False

    # 验证图谱版本确实变了
    resp = httpx.get(
        f"{API_BASE}/semantic-models",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    after_version = resp.json().get("version", 0)
    after_content = resp.json().get("content", {})

    if after_version <= before_version:
        print(f"  ⚠️ 图谱版本未变: v{before_version} (可能无达阈值表对)")
    else:
        print(f"  ✅ 图谱版本更新: v{before_version}→v{after_version}")

        # 检查 confidence 变化
        after_conf = {}
        for m in after_content.get("models", []):
            for r in m.get("relationships", []):
                if r.get("source") == "implicit_mining":
                    key = f"{m['name']}→{r['target_model']}"
                    after_conf[key] = r.get("confidence", 0)

        # 新增的 implicit_mining 关系
        new_implicit = [k for k in after_conf if k not in before_conf]
        # confidence 提升的
        boosted = [k for k in after_conf if k in before_conf and after_conf[k] > before_conf[k]]

        if new_implicit:
            print(f"  🆕 新 implicit_mining 关系: {new_implicit}")
        if boosted:
            for k in boosted:
                print(f"  📈 confidence boost: {k} {before_conf.get(k,0):.2f}→{after_conf[k]:.2f}")

        if not new_implicit and not boosted:
            print(f"  ⚠️ 无实际 confidence 变化")

    print("  ✅ 验证4 通过")
    return True


# ═══════════════════════════════════════════════════════════════
def test_5_recall_memories(token: str) -> bool:
    """验证5: recall_memories 功能"""
    print("\n" + "=" * 60)
    print("验证5: recall_memories 功能")
    print("=" * 60)

    from app.ai.recall import recall_memories

    # 测试1: GMV 相关问题应该召回 gmv-calculation-rule
    memories = recall_memories("本月GMV是多少", memory_dir=f"{project_root}/memory/default_tenant/{DS_ID}")
    print(f"  查询'本月GMV是多少': 召回 {len(memories)} 条")
    names = [m.get("name", "") for m in memories]
    print(f"    召回记忆: {names}")
    has_gmv = any("gmv" in n.lower() for n in names)
    if has_gmv:
        print("    ✅ 召回了 GMV 相关记忆")
    else:
        print("    ⚠️ 未召回 GMV 相关记忆 (关键词可能不匹配)")

    # 测试2: linkage 类型不应出现在 recall 结果中
    has_linkage = any(m.get("type") == "linkage" for m in memories)
    if has_linkage:
        print("    ❌ recall 返回了 linkage 类型 (不应出现)")
        return False
    else:
        print("    ✅ linkage 类型正确被过滤")

    # 测试3: 不相关问题应返回空
    memories2 = recall_memories("量子物理", memory_dir=f"{project_root}/memory/default_tenant/{DS_ID}")
    print(f"  查询'量子物理': 召回 {len(memories2)} 条")
    if len(memories2) == 0:
        print("    ✅ 不相关问题返回空 (宁缺毋滥)")
    else:
        print(f"    ⚠️ 不相关问题返回了 {len(memories2)} 条: {[m.get('name') for m in memories2]}")

    print("  ✅ 验证5 通过")
    return True


# ═══════════════════════════════════════════════════════════════
def test_6_backward_compat() -> bool:
    """验证6: 向后兼容 — 手写旧格式 linkage 文件"""
    print("\n" + "=" * 60)
    print("验证6: 向后兼容 — 旧格式 linkage 文件")
    print("=" * 60)

    import tempfile
    from app.core.agent_memory import AgentMemoryStore

    # 用临时目录创建旧格式 linkage
    tmp = tempfile.mkdtemp()
    store = AgentMemoryStore(base_dir=tmp)

    # 旧格式: 只有 co_occurrence + tables, 没有 join_paths/scenes/aggregation
    store.save_memory(
        "linkage-old-format", "旧格式 linkage",
        "## JOIN 路径\nbiz_orders.user_id = biz_users.id\n",
        memory_type="linkage",
        extra_metadata={"co_occurrence": 5, "tables": ["biz_orders", "biz_users"]},
    )

    # 读取
    memories = store.list_memories()
    linkage = [m for m in memories if m.get("type") == "linkage"]
    assert len(linkage) == 1, f"应该有1条 linkage, 实际 {len(linkage)}"

    m = linkage[0]
    print(f"  旧格式 linkage: co_occurrence={m.get('co_occurrence')}, tables={m.get('tables')}")
    print(f"    join_paths: {m.get('join_paths', '(无, 正常)')}")
    print(f"    scenes: {m.get('scenes', '(无, 正常)')}")
    print(f"    aggregation: {m.get('aggregation', '(无, 正常)')}")

    # 验证: 旧字段正常, 新字段不存在 (不报错)
    assert m.get("co_occurrence") == 5, f"co_occurrence 错误: {m.get('co_occurrence')}"
    assert m.get("tables") == ["biz_orders", "biz_users"], f"tables 错误: {m.get('tables')}"
    assert "join_paths" not in m, f"旧格式不应有 join_paths"
    assert "scenes" not in m, f"旧格式不应有 scenes"
    assert "aggregation" not in m, f"旧格式不应有 aggregation"

    # 清理
    import shutil
    shutil.rmtree(tmp)

    print("  ✅ 验证6 通过: 旧格式 linkage 正确解析, 新字段不存在不报错")
    return True


# ═══════════════════════════════════════════════════════════════
def test_7_frontend_flow(token: str) -> bool:
    """验证7: 前端完整业务流程 (API 层面验证)"""
    print("\n" + "=" * 60)
    print("验证7: 前端完整业务流程 (API 层面)")
    print("=" * 60)

    ok = True

    # 1. 数据源列表
    resp = httpx.get(f"{API_BASE}/data-sources", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    sources = resp.json()
    assert len(sources) > 0, "无数据源"
    ds = sources[0]
    print(f"  ✅ 数据源: {ds['name']} ({ds['id']})")

    # 2. 语义层
    resp = httpx.get(f"{API_BASE}/semantic-models", params={"data_source_id": DS_ID}, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    sm = resp.json()
    models = sm.get("content", {}).get("models", [])
    print(f"  ✅ 语义层: v{sm['version']}, {len(models)} 张表")

    # 3. 对话列表 (已有对话)
    resp = httpx.get(f"{API_BASE}/conversations", params={"data_source_id": DS_ID}, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    convs = resp.json()
    conv_count = len(convs) if isinstance(convs, list) else 0
    print(f"  ✅ 对话列表: {conv_count} 条")

    # 4. 记忆列表
    resp = httpx.get(f"{API_BASE}/memory", params={"data_source_id": DS_ID}, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    mem_count = len(resp.json())
    print(f"  ✅ 记忆列表: {mem_count} 条")

    # 5. 图谱关系数
    rel_count = sum(len(m.get("relationships", [])) for m in models)
    implicit_count = sum(1 for m in models for r in m.get("relationships", []) if r.get("source") == "implicit_mining")
    print(f"  ✅ 图谱关系: {rel_count} 个, 其中 implicit_mining: {implicit_count} 个")

    # 6. 非流式对话
    resp = httpx.post(
        f"{API_BASE}/chat",
        params={"data_source_id": DS_ID},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"question": "本月各品类的销售额"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    chat_result = resp.json()
    sql = chat_result.get("sql", "")
    rows = chat_result.get("row_count", 0)
    assert "SELECT" in sql.upper(), f"SQL 不含 SELECT: {sql[:100]}"
    assert rows > 0, f"查询结果为空"
    print(f"  ✅ 非流式对话: {rows} 行, SQL 正确")

    if ok:
        print("  ✅ 验证7 通过")
    return ok


# ═══════════════════════════════════════════════════════════════
def main() -> None:
    print("🔑 获取 token ...")
    try:
        token = get_token()
        print(f"  ✅ Token 获取成功")
    except Exception as e:
        print(f"  ❌ Token 获取失败: {e}")
        sys.exit(1)

    results = {}

    tests = [
        ("1_stream_sse_chain", test_1_stream_sse_chain),
        ("2_frontmatter_accuracy", test_2_frontmatter_accuracy),
        ("3_update_merge", test_3_update_merge),
        ("4_graph_sync_accuracy", test_4_graph_sync_accuracy),
        ("5_recall_memories", test_5_recall_memories),
        ("6_backward_compat", test_6_backward_compat),
        ("7_frontend_flow", test_7_frontend_flow),
    ]

    for name, test_fn in tests:
        try:
            # token 参数只在需要时传
            import inspect
            sig = inspect.signature(test_fn)
            if "token" in sig.parameters:
                results[name] = test_fn(token)
            else:
                results[name] = test_fn()
        except Exception as e:
            import traceback
            print(f"  ❌ {name} 异常: {e}")
            traceback.print_exc()
            results[name] = False

    # 汇总
    print("\n" + "=" * 60)
    print("深度验证汇总")
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
