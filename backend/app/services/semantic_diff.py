"""
语义层 diff 工具 (DSO-04 元数据自动刷新依赖)

从 semantic_models.py 的 diff 端点抽取, 补列级 diff。
供 diff 端点 + 元数据自动刷新 (detect_and_refresh_metadata) 复用。

设计:
  - diff_semantic_contents(old, new) 对比两个语义层 content dict
  - 表级: 新增/删除/变更表
  - 列级: 变更表里的新增/删除/变更列 (类型/语义类型变化)
  - 纯函数, 不依赖 DB (易测试)
"""
from __future__ import annotations


def diff_semantic_contents(old: dict, new: dict) -> dict:
    """对比两个语义层 content 的差异。

    Args:
        old: 旧版本 content dict (含 "models" 列表)
        new: 新版本 content dict

    Returns:
        {
            added_models: [新增表名],
            removed_models: [删除表名],
            changed_models: [{table, added_columns, removed_columns, changed_columns}],
            unchanged_models: [无变化表名],
            has_changes: bool  # 是否有任何差异 (元数据刷新用)
        }
    """
    old_models = {m["name"]: m for m in (old or {}).get("models", [])}
    new_models = {m["name"]: m for m in (new or {}).get("models", [])}

    added = sorted(set(new_models) - set(old_models))
    removed = sorted(set(old_models) - set(new_models))
    common = sorted(set(old_models) & set(new_models))

    changed_models = []
    unchanged = []
    for name in common:
        col_diff = _diff_columns(old_models[name], new_models[name])
        # 整表对比 (display_name/description 等顶层字段也算变更)
        top_changed = any(
            old_models[name].get(k) != new_models[name].get(k)
            for k in ("display_name", "description", "source", "confidence")
        )
        if col_diff["has_column_changes"] or top_changed:
            changed_models.append({"table": name, **col_diff})
        else:
            unchanged.append(name)

    has_changes = bool(added or removed or changed_models)
    return {
        "added_models": added,
        "removed_models": removed,
        "changed_models": changed_models,
        "unchanged_models": unchanged,
        "has_changes": has_changes,
    }


def _diff_columns(old_model: dict, new_model: dict) -> dict:
    """对比单个表的列差异。

    对比列名集合 (增删) + 同名列的关键属性 (data_type/semantic_type 变更)。
    """
    old_cols = {c["name"]: c for c in old_model.get("columns", [])}
    new_cols = {c["name"]: c for c in new_model.get("columns", [])}

    added_columns = sorted(set(new_cols) - set(old_cols))
    removed_columns = sorted(set(old_cols) - set(new_cols))

    # 同名列的关键属性变更 (类型/语义类型)
    common_cols = set(old_cols) & set(new_cols)
    changed_columns = []
    for col_name in common_cols:
        for attr in ("data_type", "semantic_type"):
            if old_cols[col_name].get(attr) != new_cols[col_name].get(attr):
                changed_columns.append(col_name)
                break

    return {
        "added_columns": added_columns,
        "removed_columns": removed_columns,
        "changed_columns": sorted(set(changed_columns)),
        "has_column_changes": bool(added_columns or removed_columns or changed_columns),
    }


def is_empty_diff(diff_result: dict) -> bool:
    """便捷判断: diff 结果是否无任何变化 (元数据刷新用)。"""
    return not diff_result.get("has_changes", False)
