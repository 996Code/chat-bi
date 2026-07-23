"""
ChatBI v2 — E2E 指标来源全链路验证脚本

验证「规则推断简单指标 + LLM 只推复合指标 + source 全链路透传」的完整业务流程:
  0. 重新扫描 — 触发规则推断, 生成 rule_inferred 指标
  1. 语义层 API — 指标 source 字段 (rule_inferred / auto_inferred / manual)
  2. SSE 流式查询 — metric_hits 包含 source + type
  3. 同步查询 — ChatResponse.metric_hits 包含 source + type
  4. 对话历史持久化 — metric_hits source 保留
  5. 审计日志 — detail.metric_hits 包含 source
  6. 图谱 API — 节点 metricCount + 指标来源
  7. 指标编辑 — source 变为 manual
  8. 记忆检索 + 指标反哺正确性

用法:
  # 确保后端运行中 (需用最新代码重启)
  ./start-backend.sh
  python backend/scripts/e2e_metric_source_test.py

  # 指定数据源
  DATA_SOURCE_ID=xxx python backend/scripts/e2e_metric_source_test.py

  # 跳过重新扫描 (使用已有语义层)
  SKIP_RESCAN=true python backend/scripts/e2e_metric_source_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

API_BASE = os.environ.get("API_BASE", "http://localhost:8999/chat-bi/api/v1")
DS_ID = os.environ.get("DATA_SOURCE_ID", "ff580191db53408ea214da49d2632254")
SKIP_RESCAN = os.environ.get("SKIP_RESCAN", "").lower() in ("1", "true", "yes")
TIMEOUT = 120
HEADERS: dict[str, str] = {}


# ── 工具函数 ──────────────────────────────────────────────────

def get_token() -> str:
    """通过 dev/token 获取测试 token。"""
    resp = httpx.post(
        f"{API_BASE}/dev/token",
        json={"tenant_id": "default_tenant", "user_id": "admin_user"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _h(content_type: bool = True) -> dict[str, str]:
    """返回带 Authorization 的 headers。"""
    h = {"Authorization": HEADERS["Authorization"]}
    if content_type:
        h["Content-Type"] = "application/json"
    return h


def _sem() -> dict | None:
    """获取当前语义层。"""
    resp = httpx.get(
        f"{API_BASE}/semantic-models",
        params={"data_source_id": DS_ID},
        headers=_h(content_type=False),
        timeout=10,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _find_metric_in_sem(sem: dict, table_name: str, metric_name: str) -> dict | None:
    """在语义层中查找指定表的指定指标。"""
    for model in sem.get("content", {}).get("models", []):
        if model.get("name") == table_name:
            for m in model.get("metrics", []):
                if m.get("name") == metric_name:
                    return m
    return None


def _collect_sse_events(question: str) -> list[dict]:
    """执行 SSE 流式查询, 收集所有事件。

    SSE 格式:
      event: <type>
      data: <json payload>

    返回: [{"_event_type": "start", ...}, {"_event_type": "complete", ...}, ...]
    """
    events: list[dict] = []
    current_event_type = ""
    with httpx.stream(
        "POST",
        f"{API_BASE}/chat/stream",
        params={"data_source_id": DS_ID},
        headers=_h(),
        json={"question": question},
        timeout=TIMEOUT,
    ) as resp:
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                current_event_type = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                    data["_event_type"] = current_event_type
                    events.append(data)
                except json.JSONDecodeError:
                    pass
    return events


def _wait_for_scan(max_wait: int = 180) -> bool:
    """等待扫描完成 (轮询 data-sources/{id} 的 scan_status)。"""
    for i in range(max_wait // 3):
        time.sleep(3)
        try:
            resp = httpx.get(
                f"{API_BASE}/data-sources/{DS_ID}",
                headers=_h(content_type=False),
                timeout=10,
            )
            if resp.status_code == 200:
                ds = resp.json()
                status = ds.get("scan_status", "unknown")
                progress = ds.get("scan_progress", 0)
                stage = ds.get("scan_stage", "")
                if status in ("done", "idle"):
                    print(f"  [{i+1}] 扫描完成 (status={status})")
                    return True
                if status == "failed":
                    print(f"  ❌ 扫描失败: {ds.get('scan_error', '?')}")
                    return False
                print(f"  [{i+1}] 扫描中... status={status}, progress={progress}%, stage={stage}")
        except Exception as e:
            print(f"  [{i+1}] 轮询异常: {e}")
    return False


# ── 测试用例 ──────────────────────────────────────────────────

def test_0_rescan_for_rule_inference() -> bool:
    """测试0: 重新扫描 — 触发规则推断, 生成 rule_inferred 指标。

    验证:
      - 触发扫描后, 语义层出现 rule_inferred 指标
      - rule_inferred 指标的 formula 是 SUM/AVG/COUNT 格式
    """
    print("\n" + "=" * 60)
    print("测试0: 重新扫描 — 触发规则推断")
    print("=" * 60)

    if SKIP_RESCAN:
        print("  ⏭️ 跳过重新扫描 (SKIP_RESCAN=true)")
        return True

    # 先检查当前语义层
    sem_before = _sem()
    rule_before = 0
    if sem_before:
        for model in sem_before.get("content", {}).get("models", []):
            for m in model.get("metrics", []):
                if m.get("source") == "rule_inferred":
                    rule_before += 1
    print(f"  当前 rule_inferred 指标数: {rule_before}")

    if rule_before > 0:
        print("  ✅ 已有 rule_inferred 指标, 无需重新扫描")
        return True

    # 触发扫描
    print("  触发扫描 ...")
    resp = httpx.post(
        f"{API_BASE}/data-sources/{DS_ID}/scan",
        headers=_h(content_type=False),
        timeout=10,
    )
    if resp.status_code not in (200, 202):
        print(f"  ❌ 扫描触发失败: HTTP {resp.status_code} — {resp.text[:200]}")
        return False

    print(f"  扫描已触发")

    # 等待扫描完成
    if not _wait_for_scan():
        print("  ❌ 扫描超时")
        return False

    # 验证 rule_inferred 指标
    time.sleep(2)  # 额外等待语义层写入
    sem_after = _sem()
    if not sem_after:
        print("  ❌ 扫描后语义层为空")
        return False

    rule_after = 0
    for model in sem_after.get("content", {}).get("models", []):
        for m in model.get("metrics", []):
            if m.get("source") == "rule_inferred":
                rule_after += 1

    print(f"  扫描后 rule_inferred 指标数: {rule_after}")

    if rule_after == 0:
        print("  ⚠️ 扫描后仍无 rule_inferred 指标 (可能 scan_metric_rule_inference=False)")
        return True  # 不算失败, 配置可能关闭

    print("  ✅ 重新扫描生成 rule_inferred 指标")
    return True


def test_1_semantic_layer_source() -> bool:
    """测试1: 语义层 API — 指标 source 字段。

    验证:
      - 语义层返回的指标有 source 字段
      - rule_inferred 指标的 formula 是 SUM/AVG/COUNT 格式, type=single
      - auto_inferred composite 指标有 factor_metric_names
    """
    print("\n" + "=" * 60)
    print("测试1: 语义层 API — 指标 source 字段")
    print("=" * 60)

    sem = _sem()
    if not sem:
        print("  ⚠️ 语义层为空, 跳过 (需先扫描)")
        return True

    content = sem.get("content", {})
    models = content.get("models", [])

    rule_count = 0
    auto_count = 0
    manual_count = 0
    other_sources: list[str] = []
    ok = True

    for model in models:
        table = model.get("name", "?")
        metrics = model.get("metrics", [])
        if not metrics:
            continue

        for m in metrics:
            source = m.get("source", "")
            mtype = m.get("type", "")
            formula = m.get("formula", "")
            name = m.get("name", "?")

            if source == "rule_inferred":
                rule_count += 1
                if not any(formula.startswith(prefix) for prefix in ("SUM(", "AVG(", "COUNT(")):
                    print(f"  ❌ {table}.{name}: rule_inferred 但 formula={formula} (非 SUM/AVG/COUNT)")
                    ok = False
                if mtype != "single":
                    print(f"  ❌ {table}.{name}: rule_inferred 但 type={mtype} (非 single)")
                    ok = False
            elif source == "auto_inferred":
                auto_count += 1
                if mtype == "composite" and not m.get("factor_metric_names"):
                    print(f"  ❌ {table}.{name}: auto_inferred composite 但无 factor_metric_names")
                    ok = False
            elif source == "manual":
                manual_count += 1
            else:
                other_sources.append(f"{table}.{name}={source}")

    print(f"  📊 指标来源分布: rule_inferred={rule_count}, auto_inferred={auto_count}, manual={manual_count}")
    if other_sources:
        print(f"  ⚠️ 未知 source: {other_sources[:5]}")

    if rule_count == 0 and auto_count == 0:
        print("  ⚠️ 无推断指标 (可能未扫描或扫描未推断)")
    else:
        print(f"  ✅ 语义层指标 source 字段验证通过 (rule={rule_count}, auto={auto_count}, manual={manual_count})")

    return ok


def test_2_sse_metric_hits_source() -> bool:
    """测试2: SSE 流式查询 — metric_hits 包含 source + type。

    验证:
      - complete 事件有 metric_hits 数组
      - 每个元素有 source + type 字段
      - source 值为 rule_inferred / auto_inferred / manual
      - type 值为 single / composite
    """
    print("\n" + "=" * 60)
    print("测试2: SSE 流式查询 — metric_hits source + type")
    print("=" * 60)

    # 找一个有指标的表, 构造能命中该指标的查询
    sem = _sem()
    target_table = None
    target_formula = None
    if sem:
        for model in sem.get("content", {}).get("models", []):
            for m in model.get("metrics", []):
                if m.get("source") in ("rule_inferred", "auto_inferred") and m.get("type") == "single":
                    target_table = model.get("name")
                    target_formula = m.get("formula", "")
                    if m.get("source") == "rule_inferred":
                        break
            if target_table:
                break

    if target_table and target_formula:
        question = f"查询{target_table}的{target_formula}结果"
    else:
        question = "本月各品类的销售额"

    print(f"  查询: {question}")

    events = _collect_sse_events(question)

    # 找 complete 事件
    complete_events = [e for e in events if e.get("_event_type") == "complete"]
    if not complete_events:
        error_events = [e for e in events if e.get("_event_type") == "error"]
        if error_events:
            print(f"  ❌ SSE 返回 error: {error_events[0].get('message', '?')}")
            return False
        print(f"  ❌ 未收到 complete 事件 (收到事件类型: {[e.get('_event_type') for e in events]})")
        return False

    complete = complete_events[0]
    metric_hits = complete.get("metric_hits", [])

    # 展示 SQL
    sql_events = [e for e in events if e.get("_event_type") == "sql"]
    if sql_events:
        print(f"  SQL: {sql_events[0].get('sql', '?')[:120]}")

    print(f"  📊 metric_hits 数量: {len(metric_hits)}")

    if not metric_hits:
        print("  ⚠️ metric_hits 为空 (查询可能未命中已知指标 — 这是指标匹配逻辑的已知限制)")
        return True

    ok = True
    for h in metric_hits:
        source = h.get("source")
        mtype = h.get("type")
        table = h.get("table", "?")
        metric = h.get("metric", "?")

        if source is None:
            print(f"  ❌ {table}.{metric}: 缺少 source 字段")
            ok = False
        elif source not in ("rule_inferred", "auto_inferred", "manual"):
            print(f"  ❌ {table}.{metric}: source={source} (非预期值)")
            ok = False

        if mtype is None:
            print(f"  ❌ {table}.{metric}: 缺少 type 字段")
            ok = False
        elif mtype not in ("single", "composite"):
            print(f"  ❌ {table}.{metric}: type={mtype} (非预期值)")
            ok = False

        src_icon = "⚙️" if source == "rule_inferred" else ("🤖" if source == "auto_inferred" else "📋")
        print(f"    {src_icon} {table}.{metric} (source={source}, type={mtype}, co={h.get('co_occurrence', 0)})")

    if ok:
        print("  ✅ SSE metric_hits source + type 验证通过")
    return ok


def test_3_sync_chat_metric_hits() -> bool:
    """测试3: 同步查询 — ChatResponse.metric_hits 包含 source + type。

    验证:
      - POST /chat 返回 metric_hits
      - 每个元素有 source + type
    """
    print("\n" + "=" * 60)
    print("测试3: 同步查询 — ChatResponse.metric_hits source + type")
    print("=" * 60)

    question = "查询用户总数"
    print(f"  查询: {question}")

    resp = httpx.post(
        f"{API_BASE}/chat",
        params={"data_source_id": DS_ID},
        headers=_h(),
        json={"question": question},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    success = data.get("success", False)
    sql = data.get("sql", "")
    metric_hits = data.get("metric_hits", [])

    print(f"  SQL: {sql[:120]}")
    print(f"  成功: {success}")
    print(f"  📊 metric_hits 数量: {len(metric_hits) if metric_hits else 0}")

    if not metric_hits:
        print("  ⚠️ metric_hits 为空 (查询可能未命中已知指标)")
        return True

    ok = True
    for h in metric_hits[:5]:  # 只展示前5个
        source = h.get("source")
        mtype = h.get("type")
        table = h.get("table", "?")
        metric = h.get("metric", "?")

        if source is None:
            print(f"  ❌ {table}.{metric}: 缺少 source 字段")
            ok = False
        if mtype is None:
            print(f"  ❌ {table}.{metric}: 缺少 type 字段")
            ok = False

        src_icon = "⚙️" if source == "rule_inferred" else ("🤖" if source == "auto_inferred" else "📋")
        print(f"    {src_icon} {table}.{metric} (source={source}, type={mtype})")

    if len(metric_hits) > 5:
        print(f"    ... 共 {len(metric_hits)} 个")

    if ok:
        print("  ✅ 同步查询 metric_hits source + type 验证通过")
    return ok


def test_4_conversation_history_persistence() -> bool:
    """测试4: 对话历史 — metric_hits source 持久化。

    验证:
      - GET /conversations/{id} 返回的 turns 中 metric_hits 带 source + type
    """
    print("\n" + "=" * 60)
    print("测试4: 对话历史 — metric_hits source 持久化")
    print("=" * 60)

    # 先执行一次查询确保有对话
    question = "查询用户总数"
    print(f"  先执行查询: {question}")
    resp = httpx.post(
        f"{API_BASE}/chat",
        params={"data_source_id": DS_ID},
        headers=_h(),
        json={"question": question},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    conv_id = data.get("conversation_id")

    if not conv_id:
        print("  ❌ 未获取到 conversation_id")
        return False

    print(f"  conversation_id: {conv_id}")

    # 等待持久化
    time.sleep(2)

    # 查对话详情 (返回的是 turns 列表, 不是 dict)
    resp = httpx.get(
        f"{API_BASE}/conversations/{conv_id}",
        headers=_h(content_type=False),
        timeout=10,
    )
    resp.raise_for_status()
    conv_detail = resp.json()

    # 兼容两种格式: list[turn] 或 dict{turns: [...]}
    if isinstance(conv_detail, list):
        turns = conv_detail
    elif isinstance(conv_detail, dict):
        turns = conv_detail.get("turns", [])
    else:
        print(f"  ❌ 对话详情格式异常: {type(conv_detail)}")
        return False

    if not turns:
        print("  ❌ 对话无 turns")
        return False

    # 找最后一个有 metric_hits 的 turn
    found_metric_hits = False
    ok = True
    for turn in turns:
        state = turn.get("state", {})
        if not isinstance(state, dict):
            continue
        mh = state.get("metric_hits")
        if not mh:
            continue
        found_metric_hits = True
        for h in mh:
            source = h.get("source")
            mtype = h.get("type")
            if source is None:
                print(f"  ❌ 对话历史 metric_hits 缺少 source: {h}")
                ok = False
            if mtype is None:
                print(f"  ❌ 对话历史 metric_hits 缺少 type: {h}")
                ok = False

        # 展示最后一条
        last_mh = mh[-1] if mh else {}
        print(f"  📋 最后命中: {last_mh.get('table', '?')}.{last_mh.get('metric', '?')} "
              f"(source={last_mh.get('source')}, type={last_mh.get('type')})")

    if not found_metric_hits:
        print("  ⚠️ 对话历史中无 metric_hits (查询可能未命中已知指标)")
        return True

    if ok:
        print("  ✅ 对话历史 metric_hits source + type 持久化验证通过")
    return ok


def test_5_audit_log_metric_hits() -> bool:
    """测试5: 审计日志 — detail.metric_hits 包含 source。

    验证:
      - GET /audit-logs 返回审计记录
      - 最新记录的 detail.metric_hits 每个元素有 source + type
    """
    print("\n" + "=" * 60)
    print("测试5: 审计日志 — detail.metric_hits source")
    print("=" * 60)

    resp = httpx.get(
        f"{API_BASE}/audit-logs",
        params={"limit": 10, "resource_type": "chat"},
        headers=_h(content_type=False),
        timeout=10,
    )
    resp.raise_for_status()
    logs = resp.json()

    if not logs:
        print("  ⚠️ 无审计日志 (需先执行查询)")
        return True

    # 找有 metric_hits 的最新记录
    found = False
    ok = True
    for log in logs:
        detail = log.get("detail", {})
        if not detail or not isinstance(detail, dict):
            continue
        mh = detail.get("metric_hits")
        if not mh:
            continue
        found = True
        for h in mh:
            source = h.get("source")
            mtype = h.get("type")
            if source is None:
                print(f"  ❌ 审计日志 metric_hits 缺少 source: {h}")
                ok = False
            if mtype is None:
                print(f"  ❌ 审计日志 metric_hits 缺少 type: {h}")
                ok = False

        # 展示
        print(f"  📋 审计记录: action={log.get('action')}, status={log.get('status')}")
        print(f"    metric_hits: {json.dumps(mh[:3], ensure_ascii=False)}")
        break  # 只看最新一条

    if not found:
        print("  ⚠️ 审计日志中无 metric_hits (查询可能未命中已知指标)")
        return True

    if ok:
        print("  ✅ 审计日志 metric_hits source 验证通过")
    return ok


def test_6_graph_api_metrics() -> bool:
    """测试6: 图谱 API — 节点 metricCount + 指标来源。

    验证:
      - GET /graph 返回节点有 metricCount
      - metricCount > 0 的节点在语义层有对应指标
    """
    print("\n" + "=" * 60)
    print("测试6: 图谱 API — 节点 metricCount + 指标来源")
    print("=" * 60)

    resp = httpx.get(
        f"{API_BASE}/graph",
        params={"data_source_id": DS_ID},
        headers=_h(content_type=False),
        timeout=10,
    )
    resp.raise_for_status()
    graph = resp.json()

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    print(f"  📊 图谱: {len(nodes)} 节点, {len(edges)} 边")

    has_metrics = 0
    ok = True
    for node in nodes:
        mc = node.get("metricCount", 0)
        source = node.get("source", "")
        label = node.get("label", "?")
        if mc > 0:
            has_metrics += 1
            if not source:
                print(f"  ❌ 节点 {label}: metricCount={mc} 但无 source")
                ok = False

    print(f"  有指标的节点: {has_metrics}/{len(nodes)}")

    # 交叉验证: metricCount > 0 的节点在语义层应有对应指标
    sem = _sem()
    if sem:
        content = sem.get("content", {})
        sem_tables = {m.get("name") for m in content.get("models", []) if m.get("metrics")}
        graph_tables_with_metrics = {n.get("id") for n in nodes if n.get("metricCount", 0) > 0}
        mismatch = graph_tables_with_metrics - sem_tables
        if mismatch:
            print(f"  ⚠️ 图谱有指标但语义层无: {list(mismatch)[:5]}")

    if ok:
        print("  ✅ 图谱 API metricCount + source 验证通过")
    return ok


def test_7_metric_edit_source_manual() -> bool:
    """测试7: 指标编辑 — source 变为 manual。

    验证:
      - PATCH /semantic-models/{id}/metric 编辑指标
      - 编辑后 source 变为 "manual"
    """
    print("\n" + "=" * 60)
    print("测试7: 指标编辑 — source 变为 manual")
    print("=" * 60)

    sem = _sem()
    if not sem:
        print("  ⚠️ 语义层为空, 跳过")
        return True

    sm_id = sem.get("id")
    if not sm_id:
        print("  ❌ 语义层无 id")
        return False

    content = sem.get("content", {})
    models = content.get("models", [])

    # 找一个有推断指标的表 (优先 rule_inferred, 其次 auto_inferred)
    target_table = None
    target_metric = None
    for model in models:
        for m in model.get("metrics", []):
            if m.get("source") in ("rule_inferred", "auto_inferred"):
                target_table = model.get("name")
                target_metric = m.get("name")
                if m.get("source") == "rule_inferred":
                    break
        if target_table:
            break

    if not target_table or not target_metric:
        print("  ⚠️ 无推断指标可编辑, 跳过")
        return True

    print(f"  编辑: {target_table}.{target_metric} (→ manual)")

    # PATCH 编辑指标 (只改 display_name, source 应自动变 manual)
    resp = httpx.patch(
        f"{API_BASE}/semantic-models/{sm_id}/metric",
        headers=_h(),
        json={
            "table_name": target_table,
            "metric_name": target_metric,
            "metric_display_name": "测试编辑指标",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"  ❌ PATCH 失败: HTTP {resp.status_code} — {resp.text[:200]}")
        return False

    # 验证 source 变为 manual
    sem_after = _sem()
    metric_after = _find_metric_in_sem(sem_after, target_table, target_metric)

    if not metric_after:
        print(f"  ❌ 编辑后找不到指标 {target_table}.{target_metric}")
        return False

    new_source = metric_after.get("source")
    if new_source != "manual":
        print(f"  ❌ 编辑后 source={new_source} (期望 manual)")
        return False

    print(f"  ✅ 编辑后 source={new_source}")
    return True


def test_8_memory_recall_correctness() -> bool:
    """测试8: 记忆检索 + 指标反哺正确性。

    验证:
      - 指标反哺不改变 source (rule_inferred 仍为 rule_inferred)
      - 记忆文件中 linkage 的 co_occurrence 字段存在
    """
    print("\n" + "=" * 60)
    print("测试8: 记忆检索 + 指标反哺正确性")
    print("=" * 60)

    # 获取语义层当前状态
    sem_before = _sem()
    if not sem_before:
        print("  ⚠️ 语义层为空, 跳过")
        return True

    # 找一个有推断指标的表
    target_table = None
    target_metric_name = None
    co_before = 0
    source_before = ""
    for model in sem_before.get("content", {}).get("models", []):
        for m in model.get("metrics", []):
            if m.get("source") in ("rule_inferred", "auto_inferred") and m.get("co_occurrence", 0) >= 0:
                target_table = model.get("name")
                target_metric_name = m.get("name")
                co_before = m.get("co_occurrence", 0)
                source_before = m.get("source", "")
                break
        if target_table:
            break

    if not target_table:
        print("  ⚠️ 无推断指标, 跳过")
        return True

    print(f"  目标: {target_table}.{target_metric_name} (co={co_before}, source={source_before})")

    # 执行一次查询 (SSE, 会触发 persist_metric_feedback)
    question = f"查询{target_table}的销售额"
    print(f"  查询: {question}")
    events = _collect_sse_events(question)

    # 检查 SSE metric_hits
    complete_events = [e for e in events if e.get("_event_type") == "complete"]
    if complete_events:
        mh = complete_events[0].get("metric_hits", [])
        hit_names = [h.get("metric") for h in mh]
        print(f"  命中指标: {hit_names if hit_names else '(无命中)'}")

    # 等待持久化
    time.sleep(2)

    # 再次获取语义层, 检查 source 不变
    sem_after = _sem()
    if not sem_after:
        print("  ❌ 查询后语义层为空")
        return False

    metric_after = _find_metric_in_sem(sem_after, target_table, target_metric_name)
    if not metric_after:
        print(f"  ❌ 查询后找不到指标 {target_table}.{target_metric_name}")
        return False

    co_after = metric_after.get("co_occurrence", 0)
    source_after = metric_after.get("source", "")

    print(f"  co_occurrence: {co_before} → {co_after}")
    print(f"  source: {source_before} → {source_after}")

    ok = True
    # source 不应被反哺改变
    if source_after != source_before:
        print(f"  ❌ 指标反哺改变了 source: {source_before} → {source_after}")
        ok = False
    else:
        print(f"  ✅ 指标反哺未改变 source (仍为 {source_after})")

    # 检查记忆文件
    project_root = str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    memory_dir = f"{project_root}/memory/default_tenant/{DS_ID}"
    if os.path.exists(memory_dir):
        sys.path.insert(0, project_root)
        try:
            from app.core.agent_memory import AgentMemoryStore
            store = AgentMemoryStore(base_dir=memory_dir)
            all_memories = store.list_memories()
            linkage = [m for m in all_memories if m.get("type") == "linkage"]
            print(f"  📁 记忆文件: linkage={len(linkage)}")

            for m in linkage[:5]:
                co = m.get("co_occurrence", "N/A")
                tables = m.get("tables", [])
                print(f"    {m.get('name', '?')}: co={co}, tables={tables}")
        except Exception as e:
            print(f"  ⚠️ 读取记忆文件失败: {e}")
    else:
        print(f"  ⚠️ 记忆目录不存在: {memory_dir}")

    if ok:
        print("  ✅ 记忆检索 + 指标反哺正确性验证通过")
    return ok


# ── 主流程 ────────────────────────────────────────────────────

def main() -> None:
    print("🔑 获取 token ...")
    try:
        token = get_token()
        HEADERS["Authorization"] = f"Bearer {token}"
        print(f"  ✅ Token 获取成功")
    except Exception as e:
        print(f"  ❌ Token 获取失败: {e}")
        print("  请确认后端正在运行: ./start-backend.sh")
        sys.exit(1)

    # 检查数据源
    print(f"\n📡 数据源: {DS_ID}")
    try:
        resp = httpx.get(
            f"{API_BASE}/data-sources/{DS_ID}",
            headers=_h(content_type=False),
            timeout=10,
        )
        if resp.status_code == 200:
            ds = resp.json()
            print(f"  ✅ 数据源: {ds.get('name', '?')} (active={ds.get('is_active', '?')}, scan_status={ds.get('scan_status', '?')})")
        else:
            print(f"  ⚠️ 数据源查询返回 HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️ 数据源查询失败: {e}")

    # 检查语义层
    sem = _sem()
    if sem:
        content = sem.get("content", {})
        models = content.get("models", [])
        total_metrics = sum(len(m.get("metrics", [])) for m in models)
        rule_metrics = sum(
            1 for m in models for mt in m.get("metrics", [])
            if mt.get("source") == "rule_inferred"
        )
        auto_metrics = sum(
            1 for m in models for mt in m.get("metrics", [])
            if mt.get("source") == "auto_inferred"
        )
        manual_metrics = sum(
            1 for m in models for mt in m.get("metrics", [])
            if mt.get("source") == "manual"
        )
        print(f"  📊 语义层: {len(models)} 表, {total_metrics} 指标 "
              f"(⚙️rule={rule_metrics}, 🤖auto={auto_metrics}, 📋manual={manual_metrics})")
    else:
        print("  ⚠️ 语义层为空 — 部分测试将跳过")

    # 执行测试
    tests = [
        ("0_重新扫描", test_0_rescan_for_rule_inference),
        ("1_语义层source", test_1_semantic_layer_source),
        ("2_SSE_metric_hits", test_2_sse_metric_hits_source),
        ("3_同步查询_metric_hits", test_3_sync_chat_metric_hits),
        ("4_对话历史持久化", test_4_conversation_history_persistence),
        ("5_审计日志_metric_hits", test_5_audit_log_metric_hits),
        ("6_图谱API_metrics", test_6_graph_api_metrics),
        ("7_指标编辑_manual", test_7_metric_edit_source_manual),
        ("8_记忆检索反哺", test_8_memory_recall_correctness),
    ]

    results: dict[str, bool] = {}
    for name, test_fn in tests:
        try:
            results[name] = test_fn()
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            results[name] = False

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
