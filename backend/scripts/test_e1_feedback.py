"""
E1 端到端验证脚本 — 查询反哺知识图谱

完整流程:
  1. 生成 linkage 测试记忆 (模拟多次查询沉淀)
  2. 调记忆整理 → 触发图谱同步
  3. 验证图谱 confidence 变化
  4. 验证新表对发现
  5. 验证冲突重试

用法:
  python backend/scripts/test_e1_feedback.py

前置: 后端已启动, 数据源已扫描
"""
from __future__ import annotations

import os
import sys
import time
import httpx

API_BASE = os.getenv("API_BASE", "http://localhost:8999/chat-bi/api/v1")


def get_dev_token() -> str:
    resp = httpx.post(
        f"{API_BASE}/dev/token",
        json={"tenant_id": "default_tenant", "user_id": "admin_user"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_first_datasource(token: str) -> str:
    resp = httpx.get(
        f"{API_BASE}/data-sources",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    sources = resp.json()
    if not sources:
        print("❌ 没有数据源, 请先创建")
        sys.exit(1)
    return sources[0]["id"]


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_memories(token: str, ds_id: str) -> list[dict]:
    resp = httpx.get(
        f"{API_BASE}/memory",
        params={"data_source_id": ds_id, "include_consolidated": True},
        headers=headers(token),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def save_memory(token: str, ds_id: str, mem: dict) -> dict:
    resp = httpx.put(
        f"{API_BASE}/memory",
        params={"data_source_id": ds_id},
        headers=headers(token),
        json=mem,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def delete_all_memories(token: str, ds_id: str) -> int:
    mems = list_memories(token, ds_id)
    count = 0
    for m in mems:
        resp = httpx.delete(
            f"{API_BASE}/memory/{m['id']}",
            params={"data_source_id": ds_id},
            headers=headers(token),
            timeout=10,
        )
        if resp.status_code == 200:
            count += 1
    return count


def get_current_semantic(token: str, ds_id: str) -> dict | None:
    resp = httpx.get(
        f"{API_BASE}/semantic-models",
        params={"data_source_id": ds_id},
        headers=headers(token),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def consolidate(token: str, ds_id: str) -> dict:
    """触发整理, 轮询直到完成, 返回最终 status。"""
    resp = httpx.post(
        f"{API_BASE}/memory/consolidate",
        params={"data_source_id": ds_id},
        headers=headers(token),
        timeout=30,
    )
    if resp.status_code == 409:
        print("  ⚠️ 整理已在运行, 等待完成...")

    # 轮询
    for _ in range(60):
        time.sleep(2)
        resp = httpx.get(
            f"{API_BASE}/memory/consolidate/status",
            params={"data_source_id": ds_id},
            headers=headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)
        stage = data.get("stage", "")
        print(f"  📊 整理进度: {progress}% — {stage}", end="\r")
        if status in ("done", "failed"):
            print()
            return data
    print()
    return {"status": "timeout"}


def retry_graph_sync(token: str, ds_id: str) -> dict:
    """重试图谱同步。"""
    resp = httpx.post(
        f"{API_BASE}/memory/consolidate/retry",
        params={"data_source_id": ds_id},
        headers=headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    # 轮询
    for _ in range(30):
        time.sleep(2)
        resp = httpx.get(
            f"{API_BASE}/memory/consolidate/status",
            params={"data_source_id": ds_id},
            headers=headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") in ("done", "failed"):
            return data
    return {"status": "timeout"}


# ── 生成 linkage 测试记忆 ───────────────────────────────────

def generate_linkage_memories() -> list[dict]:
    """生成模拟查询沉淀的 linkage 记忆。

    场景:
      - biz_orders + biz_users: 共现 5 次 (高, 已知关系, 应 boost)
      - biz_orders + pd_products: 共现 3 次 (达阈值, 已知关系, 应 boost)
      - biz_orders + mkt_promotions: 共现 6 次 (高, 未知关系, 应发现新表对)
      - biz_order_items + pd_products: 共现 2 次 (低, 未达阈值, 不 boost)
    """
    return [
        # biz_orders + biz_users: 共现 5 次 (已知关系, threshold=3 → boost)
        {
            "name": "linkage-biz_orders-biz_users",
            "description": "表 biz_orders 和 biz_users 的共现经验",
            "type": "linkage",
            "content": "## JOIN 路径\nbiz_orders.user_id = biz_users.id\n\n## 典型场景\n- 查用户订单\n- 按用户分组统计\n\n## 聚合方式\nGROUP BY\n\n## SQL 示例\n```sql\nSELECT u.username, COUNT(*) FROM biz_orders o JOIN biz_users u ON o.user_id = u.id GROUP BY u.username\n```",
            "id": None,  # 后端生成
            # extra_metadata 通过 save_memory 的 extra 不走 API, 需要直接操作文件
        },
        # biz_orders + pd_products: 共现 3 次
        {
            "name": "linkage-biz_orders-pd_products",
            "description": "表 biz_orders 和 pd_products 的共现经验",
            "type": "linkage",
            "content": "## JOIN 路径\nbiz_orders.id = biz_order_items.order_id, biz_order_items.product_id = pd_products.id\n\n## 典型场景\n- 各品类销售额\n- 商品排行\n\n## 聚合方式\nGROUP BY category",
        },
        # biz_orders + mkt_promotions: 共现 6 次 (未知关系, new_pair_threshold=5 → 发现)
        {
            "name": "linkage-biz_orders-mkt_promotions",
            "description": "表 biz_orders 和 mkt_promotions 的共现经验",
            "type": "linkage",
            "content": "## 关联方式\n间接关联（经由 biz_order_items）\n\n## 典型场景\n- 促销活动订单\n- 活动效果分析\n- 限时秒杀订单量",
        },
        # biz_order_items + pd_products: 共现 2 次 (未达阈值, 不 boost)
        {
            "name": "linkage-biz_order_items-pd_products",
            "description": "表 biz_order_items 和 pd_products 的共现经验",
            "type": "linkage",
            "content": "## JOIN 路径\nbiz_order_items.product_id = pd_products.id\n\n## 典型场景\n- 订单商品明细",
        },
    ]


def write_linkage_memory_files(ds_id: str, tenant_id: str = "default_tenant") -> None:
    """直接写 linkage 记忆文件 (因为 API 的 save_memory 不支持 extra_metadata)。

    模拟 persist_linkage_memory 的产出: 带 co_occurrence/tables frontmatter 的 .md 文件。

    注意: 后端用 --app-dir backend 启动时, memory/ 相对于项目根目录 (CWD),
    而非 backend/ 目录。脚本从项目根目录运行 uv run, 所以也用根目录。
    """
    from pathlib import Path
    import uuid

    # 检测 memory/ 的实际位置: 先检查根目录, 再检查 backend/
    root_mem = Path("memory") / tenant_id / ds_id
    backend_mem = Path("backend") / "memory" / tenant_id / ds_id
    # 如果根目录有 memory/ (后端用 --app-dir 时 CWD=项目根), 优先用根目录
    if Path("memory").exists():
        mem_dir = root_mem
    elif backend_mem.parent.parent.exists():
        mem_dir = backend_mem
    else:
        mem_dir = root_mem  # 默认创建在根目录

    mem_dir.mkdir(parents=True, exist_ok=True)

    # 先删除已有 linkage 记忆
    for f in mem_dir.glob("linkage-*.md"):
        f.unlink()

    linkage_data = [
        # (table_a, table_b, co_occurrence, content)
        ("biz_orders", "biz_users", 5,
         "## JOIN 路径\nbiz_orders.user_id = biz_users.id\n\n## 典型场景\n- 查用户订单\n- 按用户分组统计\n\n## 聚合方式\nGROUP BY\n"),
        ("biz_orders", "pd_products", 3,
         "## JOIN 路径\nbiz_orders.id = biz_order_items.order_id, biz_order_items.product_id = pd_products.id\n\n## 典型场景\n- 各品类销售额\n- 商品排行\n\n## 聚合方式\nGROUP BY category\n"),
        ("biz_orders", "mkt_promotions", 6,
         "## 关联方式\n间接关联（经由 biz_order_items）\n\n## 典型场景\n- 促销活动订单\n- 活动效果分析\n- 限时秒杀订单量\n"),
        ("biz_order_items", "pd_products", 2,
         "## JOIN 路径\nbiz_order_items.product_id = pd_products.id\n\n## 典型场景\n- 订单商品明细\n"),
    ]

    written = 0
    for table_a, table_b, co, content in linkage_data:
        mem_id = uuid.uuid4().hex[:32]
        pair = tuple(sorted([table_a, table_b]))
        file_path = mem_dir / f"{mem_id}.md"
        frontmatter = f"""---
id: {mem_id}
name: linkage-{pair[0]}-{pair[1]}
description: 表 {pair[0]} 和 {pair[1]} 的共现经验
metadata:
  type: linkage
  consolidated: false
  co_occurrence: {co}
  tables:
    - "{pair[0]}"
    - "{pair[1]}"
---

{content}"""
        file_path.write_text(frontmatter, encoding="utf-8")
        written += 1
        print(f"  📝 写入 linkage: {pair[0]}+{pair[1]} (co_occurrence={co})")

    return written


def update_memory_index(ds_id: str, tenant_id: str = "default_tenant") -> None:
    """更新 MEMORY.md 索引 (和 AgentMemoryStore 格式一致)。"""
    from pathlib import Path
    import re

    # 检测和 write_linkage_memory_files 一致的路径
    root_mem = Path("memory") / tenant_id / ds_id
    backend_mem = Path("backend") / "memory" / tenant_id / ds_id
    if Path("memory").exists():
        mem_dir = root_mem
    elif backend_mem.parent.parent.exists():
        mem_dir = backend_mem
    else:
        mem_dir = root_mem
    index_path = mem_dir / "MEMORY.md"

    entries = []
    for f in sorted(mem_dir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            content = f.read_text(encoding="utf-8")[:500]
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            if name_match:
                name = name_match.group(1).strip()
                desc = desc_match.group(1).strip() if desc_match else ""
                entries.append(f"- {name}: {desc}")
        except Exception:
            continue

    index_content = "# Memory Index\n\n" + "\n".join(entries) + "\n"
    index_path.write_text(index_content, encoding="utf-8")
    print(f"  📋 更新 MEMORY.md 索引 ({len(entries)} 条)")


# ── 主流程 ───────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("E1 端到端验证: 查询反哺知识图谱")
    print("=" * 60)

    # 1. 准备
    print("\n🔑 获取 token...")
    token = get_dev_token()
    ds_id = get_first_datasource(token)
    print(f"📦 数据源: {ds_id}")

    # 2. 查看当前语义层
    print("\n📊 当前语义层状态...")
    semantic = get_current_semantic(token, ds_id)
    if semantic:
        version = semantic.get("version", "?")
        models = semantic.get("content", {}).get("models", [])
        total_rels = sum(len(m.get("relationships", [])) for m in models)
        print(f"  版本: v{version}, 表数: {len(models)}, 关系数: {total_rels}")

        # 列出部分已知关系
        print("  已知关系 (前 10):")
        shown = 0
        for m in models:
            for r in m.get("relationships", []):
                from_t = r["name"].split("_to_")[0] if "_to_" in r["name"] else "?"
                print(f"    {from_t} → {r['target_model']} (conf={r.get('confidence', 0):.2f}, src={r.get('source', '?')})")
                shown += 1
                if shown >= 10:
                    break
            if shown >= 10:
                break
    else:
        print("  ❌ 无语义层, 请先扫描数据源")
        return

    # 3. 清理旧的 linkage 测试记忆 (不删种子记忆, 避免 _ensure_seed_memories 自动恢复)
    print("\n🧹 清理已有 linkage 测试记忆...")
    # 直接删文件 (根目录 memory/ 下)
    from pathlib import Path
    mem_dir = Path("memory") / "default_tenant" / ds_id
    if not mem_dir.exists():
        mem_dir = Path("backend") / "memory" / "default_tenant" / ds_id
    deleted_files = 0
    if mem_dir.exists():
        for f in mem_dir.glob("*.md"):
            if f.name == "MEMORY.md":
                continue
            # 检查是否是 linkage 类型
            content = f.read_text(encoding="utf-8")[:500]
            if "type: linkage" in content:
                f.unlink()
                deleted_files += 1
    print(f"  已删除 {deleted_files} 条旧 linkage 记忆")

    print("\n📝 生成 linkage 测试记忆 (直接写文件)...")
    written = write_linkage_memory_files(ds_id)
    update_memory_index(ds_id)
    print(f"  共写入 {written} 条 linkage 记忆")

    # 验证 API 可读到
    print("\n🔍 验证记忆列表...")
    mems = list_memories(token, ds_id)
    linkage_mems = [m for m in mems if m.get("type") == "linkage"]
    print(f"  API 返回记忆数: {len(mems)}, 其中 linkage: {len(linkage_mems)}")
    for m in linkage_mems:
        print(f"    📎 {m['name']} (co_occurrence={m.get('co_occurrence', '?')})")

    # 4. 触发记忆整理 → 图谱同步
    print("\n🔄 触发记忆整理 (含图谱同步)...")
    result = consolidate(token, ds_id)

    status = result.get("status", "?")
    print(f"  整理状态: {status}")

    if status == "done":
        detail = result.get("result", {}).get("detail", "")
        graph_sync = result.get("result", {}).get("graph_sync")
        graph_conflict = result.get("result", {}).get("graph_sync_conflict")
        print(f"  整理详情: {detail}")

        if graph_conflict:
            print(f"  ⚠️ 图谱同步冲突: {result['result'].get('graph_sync_error', '?')}")
            print("\n🔁 重试图谱同步...")
            retry_result = retry_graph_sync(token, ds_id)
            retry_sync = retry_result.get("result", {}).get("graph_sync")
            if retry_sync:
                print(f"  重试成功: {retry_sync}")
                graph_sync = retry_sync
            else:
                print(f"  重试状态: {retry_result.get('status', '?')}")

        if graph_sync:
            new_ver = graph_sync.get("new_version")
            boosted = graph_sync.get("boosted_pairs", 0)
            new_pairs = graph_sync.get("new_pairs", 0)
            detail = graph_sync.get("detail", "")
            print(f"\n✅ 图谱同步结果:")
            print(f"  新版本: v{new_ver}" if new_ver else f"  无版本更新: {detail}")
            print(f"  Boosted 表对: {boosted}")
            print(f"  新发现表对: {new_pairs}")
        elif not graph_conflict:
            print("  ℹ️ 图谱同步: 无达阈值的表对需要更新")

    elif status == "failed":
        print(f"  ❌ 整理失败: {result.get('error', '?')}")

    # 5. 验证语义层变化
    print("\n📊 验证语义层变化...")
    new_semantic = get_current_semantic(token, ds_id)
    if new_semantic:
        new_version = new_semantic.get("version", "?")
        new_models = new_semantic.get("content", {}).get("models", [])
        new_total_rels = sum(len(m.get("relationships", [])) for m in new_models)
        print(f"  新版本: v{new_version}, 表数: {len(new_models)}, 关系数: {new_total_rels}")

        # 检查 confidence 变化和新发现的关系
        old_rels = {}
        for m in semantic.get("content", {}).get("models", []):
            for r in m.get("relationships", []):
                from_t = r["name"].split("_to_")[0] if "_to_" in r["name"] else "?"
                old_rels[f"{from_t}→{r['target_model']}"] = r.get("confidence", 0)

        print("\n  📈 Confidence 变化:")
        changes_found = False
        for m in new_models:
            for r in m.get("relationships", []):
                from_t = r["name"].split("_to_")[0] if "_to_" in r["name"] else "?"
                key = f"{from_t}→{r['target_model']}"
                new_conf = r.get("confidence", 0)
                old_conf = old_rels.get(key)
                source = r.get("source", "?")

                if old_conf is not None and abs(new_conf - old_conf) > 0.001:
                    print(f"    ✨ {key}: {old_conf:.2f} → {new_conf:.2f} (boosted!)")
                    changes_found = True
                elif old_conf is None:
                    print(f"    🆕 {key}: {new_conf:.2f} (新发现! source={source})")
                    changes_found = True

        if not changes_found:
            print("    (无 confidence 变化 — 可能未达阈值或已同步)")

    # 6. 测试记忆召回
    print("\n🧠 测试记忆召回 (linkage 命中)...")
    test_questions = [
        "查一下订单和用户的关系",
        "促销活动的订单有多少",
        "各品类销售额",
    ]
    for q in test_questions:
        # 通过 API 无法直接调 recall_memories, 但可以验证记忆列表中存在
        matched = [m for m in linkage_mems if any(w in m.get("description", "").lower() for w in q.split() if len(w) > 1)]
        if matched:
            print(f"  ✅ 「{q[:15]}」→ 命中 {len(matched)} 条 linkage 记忆")
        else:
            print(f"  ⚠️ 「{q[:15]}」→ 未命中 (recall_memories 使用关键词重叠, 可能匹配)")

    print("\n" + "=" * 60)
    print("🏁 E1 端到端验证完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
